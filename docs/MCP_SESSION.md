# A real MCP session

Recorded on the committed store with the same server command `.mcp.json` declares:

```bash
relate serve --mcp stdio --database data/kidneyloop --read-only
```

The client sent `initialize`, `tools/list`, then one `tools/call`. The replies below are copied from the server's output, trimmed to the fields that matter.

## tools/list

The server offers `read_cypher` and `get_schema`. It does not offer `write_cypher`, because the store is opened read-only.

## tools/call read_cypher

Question: which antigens block the most donors for P071?

```json
{
  "name": "read_cypher",
  "arguments": {
    "statement": "MATCH (p:Patient {id: $id})-[:HAS_ANTIBODY]->(a:Antigen)<-[:HAS_ANTIGEN]-(d:Donor) RETURN a.name AS antigen, count(DISTINCT d) AS donors_blocked ORDER BY donors_blocked DESC LIMIT 5",
    "parameters": {"id": "P071"}
  }
}
```

Rows returned:

| antigen | donors_blocked |
|---|---|
| DR12 | 71 |
| B51 | 64 |
| A2 | 61 |
| DR14 | 60 |
| A26 | 59 |

These counts include donors of every blood type. `kx why P071` counts only the 68 donors whose blood type could give to P071, so its numbers are smaller.

To reproduce, run `python scripts/mcp_demo.py` from the repository root with the relate CLI installed.
