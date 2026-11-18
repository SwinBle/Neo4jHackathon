"""Ask the committed store one question over MCP, the way an agent host would.

Run from the repository root: python scripts/mcp_demo.py [patient-id]
"""

import json
import subprocess
import sys

from kidneyloop.store import relate_cli

STATEMENT = (
    "MATCH (p:Patient {id: $id})-[:HAS_ANTIBODY]->(a:Antigen)<-[:HAS_ANTIGEN]-(d:Donor) "
    "RETURN a.name AS antigen, count(DISTINCT d) AS donors_blocked ORDER BY donors_blocked DESC LIMIT 5"
)


def main() -> None:
    patient = sys.argv[1].upper() if len(sys.argv) > 1 else "P071"
    requests = [
        {"jsonrpc": "2.0", "id": 0, "method": "initialize",
         "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "demo", "version": "0"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": "read_cypher", "arguments": {"statement": STATEMENT, "parameters": {"id": patient}}}},
    ]
    proc = subprocess.run(
        [relate_cli(), "serve", "--mcp", "stdio", "--database", "data/kidneyloop", "--read-only"],
        input="\n".join(json.dumps(r) for r in requests) + "\n", capture_output=True, text=True, check=True,
    )
    replies = {r["id"]: r for r in map(json.loads, proc.stdout.splitlines()) if "id" in r}
    print("tools:", ", ".join(t["name"] for t in replies[1]["result"]["tools"]))
    print(f"antigens blocking the most donors for {patient}:")
    for row in json.loads(replies[2]["result"]["content"][0]["text"])["rows"]:
        print(f"  {row['antigen']:<5} {row['donors_blocked']} donors")


if __name__ == "__main__":
    main()
