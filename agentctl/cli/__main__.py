"""Enables `python -m agentctl.cli <subcommand>` (used by the isolated runtime to spawn agents)."""
from agentctl.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
