# Demo video script (target 1:50)

Record at 1080p. Use a terminal at the repo root and a browser on `python -m http.server 8000`.

| Time | Screen | Say |
|---|---|---|
| 0:00 | README title | "A patient needs a kidney. Their sister wants to donate, but her blood type doesn't fit. KidneyLoop finds exchanges between pairs like theirs on a Relate graph." |
| 0:12 | `data/kidneyloop/graph.json` in the editor | "The whole pool is one Relate directory store in this repo: 300 synthetic pairs, 10 altruistic donors. Blood-type and antibody rules are nodes and relationships, so Cypher derives who can give to whom." |
| 0:30 | `kx match` output | "Cypher finds 38 thousand candidate cycles. An exact solver builds chains from the compatible arcs and picks 193 transplants, and the plan is written back to the graph as a MatchRun." |
| 0:50 | `kx why P071` | "Why wasn't P071 matched? The graph says: 86 percent of donors are ruled out by antibodies, 9 remain, and every cycle through P071 needs a patient the plan already serves." |
| 1:08 | `kx whatif --withdraw D085` | "If a donor withdraws, KidneyLoop deletes them inside a transaction, re-plans, and rolls back. The recorded plan is untouched." |
| 1:22 | Web explorer, click an exchange, withdraw D001 | "The same graph runs in the browser through Relate's WebAssembly build, with the same Cypher." |
| 1:36 | Claude Code asking a question over MCP | "And any agent can query it read-only over MCP." |
| 1:45 | README "Testing and QA" | "The solver is checked against brute force on 150 pools. Everything is synthetic, so please don't use it clinically." |
