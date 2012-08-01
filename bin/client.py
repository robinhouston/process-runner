#!/usr/bin/python

# Standard modules
import optparse
import os
import sys

# Runner module
RUNNER_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.path.pardir))
sys.path = [os.path.join(RUNNER_DIR, "lib")] + sys.path
import runner

# Handle command-line options
parser = optparse.OptionParser(usage="%prog [options] -- command [args]")
parser.add_option("-s", "--socket_path",
                action="store",
                default="/tmp/.runner-sock",
                help="socket path")
(options, args) = parser.parse_args()

with runner.Client(options.socket_path) as client:
    code, message, text = client.cmd(args)
    print "%03d %s" % (code, message)
    if text: print text
