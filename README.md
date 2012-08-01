# Overview

A simple client/server for running processes, designed for handling
long-running processes from a web app.

The server uses a `select()`-based event loop and never blocks.
Communication is over a Unix domain socket.

It only allows one process to be running at a time, by design.

# Example

    $ bin/server 2> log &          # start the server
    
    $ bin/client.rb run sleep 60   # kick off a slow process
    200 Spawned process
    nxbyxwchxjaxpied
    
    $ bin/client.rb run ls         # try to start another process
    403 Process already running
    
    # Get the output so far. In this case there is none:
    $ bin/client.rb log nxbyxwchxjaxpied
    204 No log
    
    $ bin/client.rb kill nxbyxwchxjaxpied   # kill the process
    200 Sent kill signal
    
    $ bin/client.rb status nxbyxwchxjaxpied # check its status
    200 Finished
    Command: "sleep" "60"
    Start-Time: 2012-08-01 01:27:33 UTC
    End-Time: 2012-08-01 01:27:38 UTC
    Exit-Code: 15

# Commands

* **run**

    Run a command. Returns an identifier for this process.

* **kill** *identifier*

    Send the SIGTERM signal to the specified process.

* **log** *identifier*

    Get the log (stdout and stderr combined) for the process.

* **status** [*identifier*]

    With no arguments, indicates whether any process is currently running.
    If a process is specified, indicates whether it has finished, and
    reports metadata.

* **recent**

    List recent processes, including the one that is currently running if any.
