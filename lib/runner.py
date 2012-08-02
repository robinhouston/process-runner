# -*- encoding: utf-8 -*-

from collections import deque
import datetime
import errno
import fcntl
import logging
import os
import random
import re
import select
import signal
import socket
import sys


# Internal utilities
_letters = map(chr, range(ord('a'), 1+ord('z')))
def _generate_uuid():
    return "".join([random.choice(_letters) for i in range(16)])

def _quote(word):
    return '"' + "".join([
        { "\n": r"\n", "\\": r"\\", '"': r'\"'}.get(char, char)
        for char in word
    ]) + '"'
def _quote_list(words):
    return " ".join([_quote(word) for word in words])

# An active connection
class _Conn(object):
    def __init__(self, server, conn):
        self.server = server
        self.conn = conn
        self.buf = ""
        
        self.commands = {
            "run": self.do_run,
            "kill": self.do_kill,
            "log": self.do_log,
            "status": self.do_status,
            "recent": self.do_recent,
        }
    
    def recv(self):
        data = self.conn.recv(1024)
        if data == "":
            # No more data from this client
            self.close()
            return
        
        self.buf += data
        lines = self.buf.split('\n')
        for line in lines[:-1]:
            self.cmd(*line.split('\0'))
        self.buf = lines[-1]
    
    def send(self, status, message, text=""):
        self.conn.sendall("%03d %d %s\n%s" % (status, len(text), message, text))
    
    def cmd(self, command, *args):
        logging.info("Command on fd %d: %s %r", self.conn.fileno(), command, args)
        if command in self.commands:
            try:
                command_result = self.commands[command](*args)
                self.send(*command_result)
            except Exception, e:
                self.send(500, str(e))
        else:
            self.send(404, "Unknown command: " + command)
    
    def close(self):
        logging.info("Closing connection to fd %d", self.conn.fileno())
        del self.server.conns[self.conn]
        self.conn.close()

    # Commands
    def do_status(self, uuid=None):
        s = self.server
        if uuid is None:
            if s.pid:
                return 200, "Running process", s.uuid
            else:
                return 200, "Ready"
        
        if uuid not in s.cmd:
            return 404, "Process not known"
        
        metadata = "Command: " + _quote_list(s.cmd[uuid]) + "\n"
        metadata += "Start-Time: " + s.start_time[uuid].strftime("%Y-%m-%d %H:%M:%S UTC\n")
        if uuid in s.exit_code:
            metadata += "End-Time: " + s.end_time[uuid].strftime("%Y-%m-%d %H:%M:%S UTC\n")
            metadata += "Exit-Code: " + str(s.exit_code[uuid]) + "\n"
        
        if s.pid and uuid == s.uuid:
            return 200, "Still running", metadata
        else:
            return 200, "Finished", metadata
    
    def do_log(self, process_uuid, offset=0):
        log = self.server.output.get(process_uuid)
        offset = int(offset)
        
        if log:
            return 200, "Log follows", log[offset:]
        elif log is None:
            return 404, "No log"
        else:
            return 204, "No log"
    
    def do_run(self, *args):
        ok = self.server.spawn_child(args)
        if ok:
            return 200, "Spawned process", self.server.uuid
        else:
            return 403, "Process already running"
    
    def do_kill(self, uuid):
        if self.server.uuid != uuid:
            return 404, "Not running"
        self.server.kill_child()
        return 200, "Sent kill signal"
    
    def do_recent(self):
        recent = self.server.recent
        if not recent:
            return 204, "No recent processes"
        return 200, "Listing recent processes", '\n'.join(recent) + '\n'

# A server
class Server(object):
    def __init__(self, socket_path):
        self.socket_backlog = 3 # Length of socket queue
        self.retain = 10 # How many recent processes to retain
        
        self.sock = self._sock(socket_path)
        self.wakeup_r, self.wakeup_w = self._signal_pipe()
        self.child_pipe_r, self.child_pipe_w = os.pipe()
        
        self.pid = None       # PID of running process, if any
        self.uuid = None      # UUID of running process, if any
        self.recent = deque() # UUIDs of up to self.retain recent processes
        
        self.output = {}      # Output from recently run commands
        self.exit_code = {}   # Exit codes from recently run commands
        self.cmd = {}         # Command lines from recently run commands
        self.start_time = {}  # Start times of recently run commands
        self.end_time = {}    # End times of recently run commands
        
        self.conns = {}       # Active connections to clients
    
    def _sock(self, socket_path):
        # Estabish the Unix Domain socket
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.bind(socket_path)
        except socket.error, e:
            if e.errno == errno.EADDRINUSE:
                # Socket already exists. Remove it and try again
                os.unlink(socket_path)
                sock.bind(socket_path)
        sock.listen(self.socket_backlog)
        return sock
    
    def _signal_pipe(self):
        # Set up a pipe for SIGCHLD notifications
        wakeup_r, wakeup_w = os.pipe()
        fcntl.fcntl(wakeup_w, fcntl.F_SETFL, # Make the pipe non-blocking
            fcntl.fcntl(wakeup_w, fcntl.F_GETFL, 0) | os.O_NONBLOCK)
        signal.set_wakeup_fd(wakeup_w) # Tell Python to send a byte to this pipe on signal
        signal.signal(signal.SIGCHLD, lambda x,y: None) # Stop ignoring SIGCHLD
        return wakeup_r, wakeup_w
    
    def spawn_child(self, args):
        if self.pid is not None:
            return False
        
        pid = os.fork()
        if pid:
            # This is the parent process
            self.pid = pid
            self.uuid = _generate_uuid()
            if len(self.recent) == self.retain:
                lru = self.recent.popleft()
                del self.exit_code[lru]
                del self.output[lru]
                del self.cmd[lru]
                del self.start_time[lru]
                del self.end_time[lru]
            self.recent.append(self.uuid)
            self.output[self.uuid] = ""
            self.cmd[self.uuid] = args[:]
            self.start_time[self.uuid] = datetime.datetime.utcnow()
            
            return True
        
        # In the child process
        out = self.child_pipe_w    # Keep the writer end of the child pipe;
        os.closerange(0, out)      # close every
        os.closerange(out+1, 1024) #            thing else.
        
        os.open("/dev/null", os.O_RDONLY) # Take stdin from /dev/null;
        os.dup2(out, 1)                   # direct stdout to the pipe,
        os.dup2(out, 2)                   #            and stderr too.
        
        os.execvp(args[0], args)
        # Not sure this can ever happen, but just in case...
        print >>sys.stderr, "Exec failed: " + ' '.join(args)
        os._exit(1)
    
    def kill_child(self):
        os.kill(self.pid, signal.SIGTERM)
    
    def reap_child(self):
        pid, status = os.waitpid(self.pid, 0)
        self.exit_code[self.uuid] = status
        self.end_time[self.uuid] = datetime.datetime.utcnow()
        self.pid = self.uuid = None
    
    def run(self):
        # Event loop
        while True:
            fds = [self.child_pipe_r, self.wakeup_r, self.sock] + self.conns.keys()
            try:
                r, w, x = select.select(fds, [], fds)
            except select.error, e:
                if e.args[0] == errno.EINTR:
                    # Interrupted system call, probably because SIGCHLD received
                    continue
                raise
            
            if x: logging.error("Error fds: %r", x)
            
            for fd in r:
                if fd == self.wakeup_r:
                    # We must have had a SIGCHLD
                    if self.child_pipe_r in r:
                        # Still data to read from the child. Don’t stop yet.
                        continue
                    
                    logging.info("Process %s terminates", self.uuid)
                    os.read(self.wakeup_r, 1) # Read the null byte
                    self.reap_child()
                
                elif fd == self.sock:
                    # Someone has connected to the socket
                    conn, address = self.sock.accept()
                    self.conns[conn] = _Conn(self, conn)
                    logging.info("Connection received; %d active connections", len(self.conns))
                
                elif fd in self.conns:
                    # Received data from one of our clients
                    logging.info("Command received on fd %d", fd.fileno())
                    try:
                        self.conns[fd].recv()
                    except socket.error, e:
                        if e.errno == errno.EPIPE:
                            pass # Client cut us off. That’s okay.
                        else:
                            raise
                
                elif fd == self.child_pipe_r:
                    # Received output from the active child
                    logging.debug("Data received from running process")
                    self.output[self.uuid] += os.read(self.child_pipe_r, 4096)
                
                else:
                    logging.warn("Unexpected file descriptor from select: %r", fd)

# A client
class Client(object):
    def __init__(self, socket_path):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(socket_path)
    
    def cmd(self, args):
        self.sock.sendall('\0'.join(args) + '\n')
        
        buf = ""
        while not '\n' in buf:
            buf += self.sock.recv(1024)
        
        mo = re.match(r"(\d\d\d) (\d+) ([^\n]*)\n(.*)", buf, re.DOTALL)
        if mo is None:
            raise Exception("Bad response: " + buf)
        
        status = int(mo.group(1))
        text_len = int(mo.group(2))
        message = mo.group(3)
        text = mo.group(4)
        
        while len(text) < text_len:
            text += self.sock.recv(1024)
        
        return status, message, text
    
    def close(self):
        self.sock.close()
    
    def __enter__(self):
        return self
    
    def __exit__(self, type, value, tb):
        self.close()

