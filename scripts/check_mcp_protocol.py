#!/usr/bin/env python3
"""Check MCP protocol: streamable_http_client → ClientSession → initialize() → list_tools()."""

import asyncio
import sys
from mcp.client.streamable_http import streamable_http_client
from mcp import ClientSession


async def check_tools():
    url = "http://127.0.0.1:8101/mcp"
    try:
        async with streamable_http_client(url) as (read, write):
            session = ClientSession(read, write)
            await session.initialize()
            tools = await session.list_tools()
            tool_names = [t.name for t in tools.tools]
            has_remember = "remember" in tool_names
            has_recall = "recall" in tool_names
            if has_remember and has_recall:
                print("ok:both")
            elif has_remember:
                print("ok:remember_only")
            elif has_recall:
                print("ok:recall_only")
            else:
                print("fail:none")
    except Exception as e:
        print(f"error:{e}")


if __name__ == "__main__":
    asyncio.run(check_tools())