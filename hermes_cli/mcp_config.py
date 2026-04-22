"""MCP-only CLI compatibility shim."""

from __future__ import annotations


def mcp_command(args) -> None:
    """Route the legacy `hermes mcp serve` dispatcher to the MCP server."""
    from mcp_serve import run_mcp_server

    action = getattr(args, "mcp_action", "serve")
    if action not in (None, "serve"):
        raise SystemExit(f"Unsupported MCP action: {action}")
    run_mcp_server(verbose=bool(getattr(args, "verbose", False)))
