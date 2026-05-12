"""Entry point so `python -m gates` invokes the CLI."""

import sys

from gates.cli import main

if __name__ == "__main__":
    sys.exit(main())
