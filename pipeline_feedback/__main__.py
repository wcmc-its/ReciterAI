"""`python -m pipeline_feedback` entry point — dispatches to cli.main."""
import sys
from pipeline_feedback.cli import main
if __name__ == "__main__":
    sys.exit(main())
