"""MCP stdio server entrypoint.

This module intentionally stays thin so the MCP server implementation (tool list
+ dispatch) can be reused by multiple transports:
- stdio (this file)
- remote StreamableHTTP/SSE mounted in the FastAPI app
"""

from __future__ import annotations

import anyio

from mcp.server.stdio import stdio_server

from .server_core import build_mcp_server, build_runtime


async def run_server() -> None:
    """Run the MCP server over stdio."""

    runtime = build_runtime()
    server, init_options = build_mcp_server(runtime)

    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, init_options)


def main() -> None:
    """Console script entrypoint."""

    anyio.run(run_server)


if __name__ == "__main__":
    main()
