"""Mementic MCP server entry point — no CLI dependency, stdio-only."""

from mementic.server import mcp

if __name__ == "__main__":
    mcp.run(transport="stdio")
