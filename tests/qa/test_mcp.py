import json
import subprocess
from pathlib import Path

import pytest

from kidneyloop.store import relate_cli

pytestmark = pytest.mark.qa
REPO = Path(__file__).resolve().parents[2]


def mcp_session(store: Path, calls: list[dict]) -> list[dict]:
    """Run the same server command .mcp.json declares and return one reply per request."""
    config = json.loads((REPO / ".mcp.json").read_text())["mcpServers"]["kidneyloop"]
    args = [a if a != "data/kidneyloop" else str(store) for a in config["args"]]
    lines = [
        {"jsonrpc": "2.0", "id": 0, "method": "initialize",
         "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "qa", "version": "0"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        *({"jsonrpc": "2.0", "id": i, **c} for i, c in enumerate(calls, start=1)),
    ]
    proc = subprocess.run(
        [relate_cli(), *args],
        input="\n".join(json.dumps(l) for l in lines) + "\n",
        capture_output=True, text=True, timeout=120, check=True,
    )
    replies = [json.loads(l) for l in proc.stdout.splitlines() if l.strip()]
    return sorted((r for r in replies if "id" in r), key=lambda r: r["id"])


def test_mcp_server_lists_read_tools_and_answers_a_graph_query(built_store):
    replies = mcp_session(
        built_store,
        [
            {"method": "tools/list", "params": {}},
            {"method": "tools/call", "params": {"name": "read_cypher", "arguments": {
                "statement": "MATCH (d:Donor)-[:COMPATIBLE]->(p:Patient {id: $id}) RETURN count(d) AS n",
                "parameters": {"id": "P001"}}}},
            {"method": "tools/call", "params": {"name": "write_cypher", "arguments": {"statement": "CREATE (:X)"}}},
        ],
    )
    assert replies[0]["result"]["serverInfo"]["name"] == "relate-mcp"
    tools = {t["name"] for t in replies[1]["result"]["tools"]}
    assert tools == {"read_cypher", "get_schema"}
    body = json.loads(replies[2]["result"]["content"][0]["text"])
    assert body["error"] is None and isinstance(body["rows"][0]["n"], int)
    assert replies[3]["result"]["isError"] is True
