# KidneyLoop plan

KidneyLoop finds kidney paired-donation cycles and chains in a Relate graph, and shows the path that explains every match and every non-match. Built for the Relate by Neo4j Hackathon.

All patient and donor data is synthetic. KidneyLoop is a demonstration, not a clinical tool.

## Deadline

Submissions close 6:00 PM PST, Friday 11 December 2026 (09:00 Saturday 12 December, Thailand). Target submit date: 10 December.

## Submission checklist (from the contest page and rules)

- [ ] Public GitHub repo with topic `relate` (required) and `relate-neo4j-hackathon`
- [ ] README following the contest template: use case, mode, why this mode, what the graph models, setup, where the data lives
- [ ] Persistent Relate store committed in the repo
- [ ] Working code with Relate running
- [ ] Demo video, 1 to 2 minutes, viewable without login
- [ ] Statement of Relate capabilities, third-party tools and AI tools used
- [ ] MIT license

## Judging weights

| Criterion | Points |
|---|---|
| Relate and connected context | 30 |
| Functionality and completeness | 25 |
| Usefulness | 20 |
| Documentation and reproducibility | 15 |
| Creativity | 10 |

## User and core flow

The user is a transplant coordinator running a matching round.

1. Load synthetic patient-donor pairs and altruistic donors.
2. Derive `COMPATIBLE` edges from blood-type and antibody rules stored in the graph.
3. Enumerate cycles of up to 3 pairs and chains of up to 4 starting from an altruistic donor.
4. Select the set of disjoint cycles and chains that maximizes transplants (ILP).
5. Explain why a patient is unmatched, as a graph path.
6. What-if: withdraw a donor inside a transaction, show the broken exchanges and the re-plan, then roll back.

## Graph model

Nodes:

- `Patient {id, blood_type, cpra, wait_days, region}`
- `Donor {id, blood_type, region, altruistic}`
- `BloodType {name}`
- `Antigen {name}`
- `MatchRun {id, created_at, objective, solver}`
- `Exchange {kind, size}` where kind is `cycle` or `chain`

Relationships:

- `(Donor)-[:PAIRED_WITH]->(Patient)`
- `(Donor|Patient)-[:HAS_BLOOD]->(BloodType)`
- `(BloodType)-[:ABO_CAN_GIVE_TO]->(BloodType)`
- `(Donor)-[:HAS_ANTIGEN]->(Antigen)`
- `(Patient)-[:HAS_ANTIBODY]->(Antigen)`
- `(Donor)-[:COMPATIBLE {score}]->(Patient)`, derived
- `(MatchRun)-[:SELECTED]->(Exchange)-[:INCLUDES {order}]->(Donor|Patient)`

## Decisions

| Decision | Choice | Reason |
|---|---|---|
| Language | Python | Fast to build, easy for judges to install |
| Store | Directory store, `json` codec, committed | Judges can read `graph.json`; git diff shows each matching round |
| Compatibility | ABO plus synthetic HLA antibodies | Unmatched explanations become multi-hop paths |
| Selection | Cypher enumerates, ILP (`pulp`) selects | The graph finds options, the solver decides |
| Objective | Max transplants, tie-break on high cPRA and wait time | Weights stored on `MatchRun` |
| Limits | Cycles up to 3, chains up to 4 | Source to cite in README, or state as our choice |
| Data | Seeded generator, 300 pairs and 10 altruistic donors | Reproducible runs |
| Agent | `relate serve --mcp stdio --read-only` plus `.mcp.json` and query recipes | No API key required; custom MCP tools are a stretch goal |
| Web | WASM page loads committed `graph.json` into memory | The browser build cannot open `.rl` or directory stores |
| CLI | `typer`: `kx build`, `kx match`, `kx why`, `kx whatif` | One install: `pip install -e .` |
| Tests | `pytest` | |
| Git | Feature branch per part, PR to `main` | |

## Verified facts (relate 1.1.0 on Windows 11)

- `relate-db` 1.1.0 from PyPI carries the engine; no CLI needed for Python.
- 2-cycles, 3-cycles and `*1..3` variable-length cycle patterns return correct results.
- Deleting a node in a transaction removes its cycles; `rollback()` restores them.
- A JSON directory store created by the CLI accepts writes from Python and reads back.
- The CLI sends anonymous usage metrics by default; `relate telemetry off` disables them.

## Schedule

| Dates | Work |
|---|---|
| 19-25 Nov | Generator, schema, compatibility derivation, cycle and chain enumeration, tests |
| 26 Nov-2 Dec | ILP solver, `MatchRun` persistence, `why` and `whatif`; MVP by 30 Nov |
| 3-8 Dec | WASM page, MCP setup, README |
| 9-10 Dec | Video, repo topics, GitHub Pages deploy, submit |

## Stretch

- Custom MCP tools `why_unmatched` and `what_if_withdraw`
- Supplier-style what-if across several withdrawals
