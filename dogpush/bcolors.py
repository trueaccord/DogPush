"""Constants for colorful console output."""

import sys

if sys.stdout.isatty():
    RED = '\033[91m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    PURPLE = '\033[95m'

    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'
else:
    RED = ''
    GREEN = ''
    YELLOW = ''
    BLUE = ''
    PURPLE = ''

    ENDC = ''
    BOLD = ''
    UNDERLINE = ''

FAIL = RED
WARNING = YELLOW
OK = GREEN
HEADER = PURPLE
