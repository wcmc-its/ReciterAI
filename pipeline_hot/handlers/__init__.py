"""Per-Task Lambda handlers for the hot-path Step Functions state machine.

Each handler is the entry point Step Functions invokes for a Task state.
The handler invokes its upstream script in `--emit-envelope` mode and
returns the parsed envelope dict. The next state (a DynamoDB:PutItem
SDK integration) writes the row, so a Python crash between "work done"
and "row written" cannot lose the completion signal — that gap is
owned by the state machine (D-07).
"""
