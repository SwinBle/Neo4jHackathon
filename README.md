# KidneyLoop

Find kidney paired-donation cycles and chains on a Relate graph, and show the graph path behind every match and every miss.

> All patients and donors in this repository are synthetic. KidneyLoop is a demonstration of graph-based matching, not a clinical tool.

## Use case

Many people who need a kidney have a relative or friend willing to donate, but the two are incompatible: the blood types do not fit, or the patient has antibodies against the donor's tissue type. Paired-donation programs pool these incompatible pairs and look for exchanges. Pair A's donor gives to patient B while pair B's donor gives to patient A (a cycle), or an altruistic donor starts a chain that runs through several pairs.

The coordinator who runs a matching round needs three things:

1. **The best set of exchanges.** As many transplants as possible, no person used twice, and hard-to-match patients favoured when two plans are otherwise equal.
2. **An explanation for each patient.** "Why wasn't P071 matched?" has a concrete answer: 86% of donors are ruled out by antibodies, 9 donors remain, and every cycle through P071 needs a patient the plan already serves.
3. **A safe what-if.** If donor D085 withdraws, which exchanges break, who was waiting on that kidney, and what does the re-plan look like? Answer it without touching the recorded plan.

The relationships that matter are who can give to whom (`COMPATIBLE`), why (blood-type rules and antibody to antigen links), who came together (`PAIRED_WITH`), and which exchanges a run chose (`MatchRun` to `Exchange` to the donors and patients in order). Cycles and the reasons for a miss are paths in this graph.

## How it uses Relate

- **Mode.** Directory store with the `json` codec, committed at [`data/kidneyloop/`](data/kidneyloop).
- **Why this mode.** A judge or reviewer can open `graph.json` and read every node and relationship. Each `kx match` adds a `MatchRun` to the store, so `git diff` shows what a matching round decided. Relate writes `graph.json` as one line, so run `git config diff.relate-json.textconv "python -m json.tool"` once per clone; [`.gitattributes`](.gitattributes) already points `graph.json` at that driver, and the diff then shows one changed property per line. A `.rl` file would be binary to git, and an in-memory store would vanish when the process ends.
- **What the graph models.**

| Nodes | Relationships |
|---|---|
| `Patient {id, wait_days, pool_pra}` | `(Donor)-[:PAIRED_WITH]->(Patient)` |
| `Donor {id, altruistic}` | `(Donor or Patient)-[:HAS_BLOOD]->(BloodType)` |
| `BloodType {name}` | `(BloodType)-[:ABO_CAN_GIVE_TO]->(BloodType)` |
| `Antigen {name}` | `(Donor)-[:HAS_ANTIGEN]->(Antigen)`, `(Patient)-[:HAS_ANTIBODY]->(Antigen)` |
| `MatchRun {id, seq, created_at, objective, solver, transplants, ...}` | `(Donor)-[:COMPATIBLE]->(Patient)`, derived in Cypher from the two rules above |
| `Exchange {id, kind, size}` | `(MatchRun)-[:SELECTED]->(Exchange)-[:INCLUDES {order, role}]->(Donor or Patient)` |

The blood-type and antibody rules are data in the graph, not code. Cypher derives from them every `COMPATIBLE` edge and each patient's `pool_pra`, the share of this pool's donors their antibodies rule out. Pool PRA plays the role cPRA plays in real programs, but it is not the clinical cPRA, which is computed against a reference donor population. A test checks the derived edges against an independent Python implementation of the same rules.

Relate capabilities used:

- **Embedded Python client** (`relate-db`). It builds the store, runs the matching queries, and runs the transactions.
- **Multi-hop Cypher patterns.** They enumerate 2-way and 3-way cycles, and explain a patient by walking blood-type and antibody paths.
- **Transactions with rollback.** `kx whatif` deletes a donor, repairs the plan and compares it with a full re-plan inside one transaction, then rolls it back.
- **Read-only opens.** `kx why`, `kx options` and the MCP server open the store read-only.
- **MCP server.** `relate serve --mcp stdio --read-only` gives any MCP agent `read_cypher` and `get_schema` over the store ([`.mcp.json`](.mcp.json)).
- **WebAssembly build.** The [web explorer](web/) replays `graph.json` into Relate in a browser tab and runs the same Cypher as the CLI.

## Setup and run

Prerequisites: Python 3.10 or later. The relate CLI is optional. You need it only for `kx build` (to create a `json` store) and for the MCP server.

```bash
git clone https://github.com/SwinBle/Neo4jHackathon.git kidneyloop
cd kidneyloop
python -m venv .venv
# macOS/Linux: source .venv/bin/activate     Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

Run every `kx` command from the repository root, or pass `--store <path>`: the store is found at `data/kidneyloop` relative to the current folder. `kx match` writes a new run into the committed store; `git checkout data` puts it back.

First result, read from the committed store:

```bash
kx why P071
```

```text
P071: blood type O; antibodies rule out 86% of this pool's donors (pool PRA)
Not matched in R1.
68 donors have a blood type that can give to O.
Antibody hits among them: A2 (19 donors), DR14 (19 donors), DR12 (16 donors), B51 (15 donors), DR4 (13 donors).
9 donors can give to P071 (0 altruistic).
18 cycles of up to 3 pairs pass through P071.
Reason: every one of those cycles needs a patient the plan already serves, e.g. P246 in R1-X44; P219 in R1-X42; P120 in R1-X28; P115 in R1-X41; P052 in R1-X26.
```

```bash
kx why P001                 # a matched patient: the exchange that serves them
kx whatif --withdraw D085   # repair the plan without donor D085, then roll back
kx options                  # candidate cycles and arcs for a round
kx match                    # solve again and record a new MatchRun
```

```text
If D085 withdraws from R1:
  P085 leaves the exchange with their donor
  R1-X33 breaks (cycle of 3: P085, P248, P157)
  waiting on D085 or a donor after it: P248, P157
  repair (other exchanges kept): 193 -> 190 patients matched
    lose a kidney: P157, P248
    newly matched: none
    same patient, different donor (needs a new crossmatch): none
  full re-plan instead: 192 patients matched, 155 of them moved to a different donor
  (rolled back; the store is unchanged)
```

A paired donor who withdraws takes their patient out of the exchange, because that patient no longer brings a kidney to trade. Real programs do not reshuffle accepted matches when one donor drops out, since every new donor-patient pairing needs a fresh crossmatch. So `kx whatif` keeps every exchange the withdrawal leaves intact and re-plans only the freed and unmatched patients, with the latest run's cycle and chain limits. It also reports what a from-scratch re-plan would do. Here that would match two more patients but move 155 patients to a different donor, a trade-off the coordinator can now see.

Patient and donor ids are case-insensitive. `kx match` and `kx whatif` print each stage and its time to stderr while they run. They took 30 to 60 seconds on the author's Windows laptop for the 300-pair pool. Most of that time goes to cycle enumeration in Cypher and the exact integer program. `kx why` answers in under a second.

To rebuild the store from scratch, install the relate CLI ([install guide](https://relatedb.ai/docs/get-started/install)) and run `kx build` (seed 42, 300 pairs, 10 altruistic donors), then `kx match`. `kx build` with the same seed writes a byte-identical `graph.json`. `kx match` then adds a `MatchRun` whose `created_at` is the time of the run, and it picks the same exchanges every time for the same pool: when several plans tie, the solver's input order is fixed, so the choice does not depend on row order or Python's hash seed. `kx build` refuses to replace a folder that is not a Relate store.

### Web explorer

```bash
python -m http.server 8000
```

Open <http://localhost:8000/web/>. The page loads `data/kidneyloop/graph.json` into Relate's WebAssembly build in a worker. It shows the latest run as cycle and chain diagrams you can filter and open. It explains any patient with the same text as `kx why`, plus a drawing of the cycles that could serve them, with the patient highlighted and the blocking patients marked. It can withdraw a donor in the tab's copy, and it has a Cypher console. The browser has no solver, so the page shows what breaks and points to `kx whatif` for the re-plan.

### Agents over MCP

With the relate CLI on your `PATH` (on Windows the installer puts it in `%LOCALAPPDATA%\Programs\relate`; open a new terminal after installing), any MCP host that reads `.mcp.json` (Claude Code does) can query the store read-only. Example questions for the agent:

- "Which antigens block the most donors for patients whose antibodies rule out more than 80% of donors?"
- "List the exchanges in the latest MatchRun that include an altruistic donor."
- "Why is P071 unmatched? Use the graph."

The server exposes `read_cypher` and `get_schema` only, and refuses `write_cypher`. A QA test checks both. [docs/MCP_SESSION.md](docs/MCP_SESSION.md) shows a real session answering a question about P071.

## Where the data lives

- [`data/kidneyloop/`](data/kidneyloop) is the persistent Relate store. `graph.json` holds every node and relationship, including the recorded `MatchRun` R1. `manifest.json` names the codec. `wal/` and the `.cypherlite*` markers make the directory a store, and a read-only open needs them. Open it with `relate --database data/kidneyloop --read-only` or `relate.connect("data/kidneyloop", read_only=True)`.
- [`src/kidneyloop/`](src/kidneyloop) holds the generator, store loading and derivation, cycle enumeration, solver, explanations and what-if.
- [`web/`](web) holds the explorer. `web/relate/` is the unmodified `@relate/relate-wasm` 1.1.0 package (Apache-2.0).

## How the matching works

1. `find_cycles` enumerates every 2-way and 3-way cycle with one Cypher `MATCH` per hop (38,332 on the committed store).
2. Chains are not enumerated. The same pool has 47,572 two-step chains, and enumerating three-step chains exceeded the engine's 256 MB value limit. The solver builds chains from the 19,742 `COMPATIBLE` arcs instead. It follows the position-indexed chain idea of [Dickerson, Manlove, Plaut, Sandholm and Trimble (2016)](https://arxiv.org/abs/1606.01623): one variable per arc and chain position, and a paired donor gives at position k+1 only if their own patient received at position k.
3. The integer program is solved exactly with HiGHS through `scipy.optimize.milp`. Each transplant is worth 1, plus a tie-break of at most 1/(patients+1) that favours high pool PRA and long waits. Summed over all patients, the tie-break stays below one transplant, so it never costs one.
4. Cycles are capped at 3 pairs and chains at 4 transplants. Programs cap cycle length because every transplant in a cycle needs simultaneous surgery, as surveyed in [this 2025 review](https://arxiv.org/pdf/2504.04091). The chain cap of 4 is this project's choice.

The compatibility rules are simplified: ABO blood type plus a synthetic antibody to antigen match over 32 HLA antigen names. The blood-type frequencies and antibody counts in the generator are illustrative, not population statistics.

## Testing and QA

```bash
pytest -m "not qa_full"       # 229 tests, about 35 s
pytest -m qa_full             # the committed 300-pair store, about 2 min
python scripts/mutation_check.py
```

- **Unit tests.** Blood-type and antibody rules, pool PRA, cycle uniqueness, the recorded run reading back, explanations and what-if.
- **Solver against brute force.** On 150 random small pools the solver's plan obeys every rule and equals the optimum of an independent exhaustive search.
- **Cypher against reference.** Cycles found by Cypher equal cycles found by an independent Python search on generated pools.
- **End-to-end QA** (`tests/qa`). The real CLI runs on a fresh store. Tests cover reproducible builds, the relate CLI reading what Python wrote, a recorded run that obeys every rule with no unused cycle or altruist left, read-only commands and what-if leaving the store files unchanged, error exits, the MCP server, and the web page running the same Cypher as the CLI.
- **Mutation check.** `scripts/mutation_check.py` plants eight known bugs one at a time, including a plan that changes with row order or Python's hash seed, and confirms the suite fails on each.

[docs/QA.md](docs/QA.md) lists the browser checks done by hand on the web explorer.

## Tools used

- **Relate 1.1.0.** The Python client `relate-db`, the CLI, the MCP server and `@relate/relate-wasm`.
- **Python libraries.** `scipy` (HiGHS MILP), `numpy`, `typer`, `pytest`.
- **AI tools.** This project was planned, written and tested with Claude Code (Anthropic), working with the author. The author chose the idea and the design decisions and reviewed the result.

## License

MIT, see [LICENSE](LICENSE). `web/relate/` is Apache-2.0, from the Relate project.
