# Test to check the mcp connection that is established
# Additionally it checks the remeber and reclal pipeline throught the mcp connection and verifies that it works properly

import asyncio
import os
from dotenv import load_dotenv

from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client

async def main():
    cognee_mcp_dir = os.path.abspath(".tools/cognee/cognee-mcp")

    server_params = StdioServerParameters(
        command="uv",
        args=[
            "--directory",
            cognee_mcp_dir,
            "run",
            "cognee-mcp",
            "--transport",
            "stdio",
            "--tool-mode",
            "all",
        ],
    )

    print("\nStarting Cognee MCP Server ...")


    async with stdio_client(server_params) as (read,write):
        async with ClientSession(read,write) as session:
            
            print("Initializing MCP session ...")
            await session.initialize()

            print("MCP connection succesful:\n")

            print("Fetching available Cognee tools ...")
            tool_result = await session.list_tools()

            print("\nTool schemas:\n")

            for tool in tool_result.tools:
                if tool.name == "recall":
                    print("Recall tool schema:")
                    print(tool.input_schema)
                if tool.name == "remember":
                    print("\nRemember tool schema:")
                    print(tool.input_schema)

                print(f"\n Found {len(tool_result.tools)} MCP tools:\n")
            for tool in tool_result.tools:
                print(f" - {tool.name}")

            # Keep the server alive only for this test
            print("\n✅ MCP connectivity test completed successfully.")

            print("\nStoring a test memory through MCP...")

            remember_result = await session.call_tool(
                "remember",
                {
                    "data": "Customer prefers affordable wireless headphones.",
                    "dataset_name": "mcp_smoke_test",
                    "background": False,
                    "self_improvement": True,
                },
            )

            print("\nRemember result:")
            print(remember_result.content)

            print("\nTesting actual memory recall through MCP...")

            recall_result = await session.call_tool(
                "recall",
                {
                    "query": "What headphones does the customer prefer?",
                    "datasets": "mcp_smoke_test",
                    "search_type": "HYBRID_COMPLETION",
                },
            )

            print("\nRecall result:")
            print(recall_result.content)


        



if __name__ == "__main__":
    asyncio.run(main())


