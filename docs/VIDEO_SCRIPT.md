# Demo video: script and shot list (target 1:50)

The contest rules ask that the video be the entrant's own work, so record and narrate it yourself. Everything below is set up so each shot is one command or one click.

## Before recording

1. Open a terminal at the repo root with the venv active, font size 18 or larger, window about 1280 x 720.
2. In a second terminal, run `python -m http.server 8000`, then open <http://localhost:8000/web/> in a browser at the same size. Wait for the line "Relate in this tab holds ...".
3. Run `kx match` once before recording if you changed anything, so the store has a run. It takes about 30 to 60 seconds; cut the wait in editing, or record the progress lines and speed them up.
4. Free recorders that work on Windows: the Xbox Game Bar (Win + G, record a window) or OBS Studio.

## Shots

| Time | Shot | Do | Say (suggested) |
|---|---|---|---|
| 0:00 | Web page header and "How it works" | Scroll slowly from the top | "A patient needs a kidney. Their sister wants to donate, but her blood type doesn't fit. KidneyLoop finds exchanges between pairs like theirs, on a Relate graph." |
| 0:12 | `data/kidneyloop/graph.json` in an editor | Scroll a few lines | "The whole pool is one Relate directory store in this repo: 300 synthetic pairs and 10 altruistic donors. Blood-type and antibody rules are nodes and relationships, so Cypher derives who can give to whom." |
| 0:28 | Terminal: `kx match` | Show the progress lines and the result | "Cypher finds about 38 thousand candidate cycles. An exact solver builds chains from the compatible arcs and picks 193 transplants. The plan is written back to the graph as a MatchRun." |
| 0:48 | Terminal: `kx why P071` | Show the output | "Why wasn't P071 matched? 86 percent of donors are ruled out by antibodies, 9 remain, and every cycle through P071 needs a patient the plan already serves." |
| 1:05 | Web: type P071, press Explain, scroll to the drawings | Point at the red nodes | "The browser runs the same Cypher on Relate's WebAssembly build and draws it: here are the cycles that could serve P071, and the patients already used elsewhere." |
| 1:18 | Terminal: `kx whatif --withdraw D085` | Show the output | "If a donor withdraws, KidneyLoop deletes them inside a transaction, re-plans, and rolls back. The recorded plan is untouched." |
| 1:33 | Claude Code (or another MCP host) in the repo | Ask "Why is P071 unmatched? Use the graph." | "Any agent can query the same store read-only over MCP." |
| 1:45 | README "Testing and QA" | Hold | "The solver is checked against brute force on 150 pools. All data is synthetic; this is a demo, not a clinical tool." |

## After recording

Upload to YouTube or Vimeo as public or unlisted (judges must see it without logging in), then paste the link into `docs/SUBMISSION.md` and the Devpost form.
