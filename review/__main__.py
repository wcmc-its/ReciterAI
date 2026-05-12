"""`python -m review` entry point.

Dispatches to review.cli.main which handles all subcommand routing.
"""
import sys

from review.cli import main

if __name__ == "__main__":
    sys.exit(main())
