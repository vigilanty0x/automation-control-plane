"""Compatibility forwarding CLI for the absorbed AgentMesh module."""

from agentops.routing_evidence.cli import main

__all__ = ["main"]

if __name__ == "__main__":
    raise SystemExit(main())
