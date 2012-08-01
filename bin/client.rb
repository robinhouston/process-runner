#!/usr/bin/ruby

require "socket"

# Ruby client code
@socket = UNIXSocket.new("/tmp/.runner-sock")
def cmd(args)
  @socket.puts args.join("\0")

  status_line = @socket.gets
  status_line =~ /^(\d\d\d) (\d+) (.*)\n/
  status, message = $1, $3
  text = @socket.read($2.to_i)
  
  return status, message, text
end

# Main program
status, message, text = cmd(ARGV)
puts "#{status} #{message}"
puts text if !text.empty?
