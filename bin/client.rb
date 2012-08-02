#!/usr/bin/ruby

require "socket"

# Ruby client code
class Client
  def initialize(socket_path)
    @socket = UNIXSocket.new(socket_path)
  end
  
  def cmd(args)
    @socket.puts args.join("\0")
    
    status_line = @socket.gets
    status_line =~ /^(\d\d\d) (\d+) (.*)\n/
    status, message = $1, $3
    text = @socket.read($2.to_i)
    
    return status, message, text
  end
end

# Main program
client = Client.new("/tmp/.runner-sock")
status, message, text = client.cmd(ARGV)
puts "#{status} #{message}"
puts text if !text.empty?
