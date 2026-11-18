# Devpost submission text

Copy each field into the form at <https://relate.devpost.com/>. Fields marked TODO need something only you can provide.

## Project name

KidneyLoop

## Elevator pitch

Kidney paired-donation matching on a Relate graph: failure-aware plans, a graph path behind every match and miss, verified counterfactuals and auditable runs.

## Repository

https://github.com/SwinBle/Neo4jHackathon (public, topics `relate` and `relate-neo4j-hackathon`)

## Demo video

TODO: your YouTube or Vimeo link (public or unlisted).

## About the project

### The problem

Many people who need a kidney have a relative or friend willing to donate, but the two are incompatible: the blood types don't fit, or the patient has antibodies against the donor's tissue type. Paired-donation programs pool these pairs and look for exchanges. Pair A's donor gives to patient B while B's donor gives to A (a cycle), or an altruistic donor starts a chain through several pairs. A coordinator needs the best plan, a reason for every patient left out, and a safe way to ask "what if this donor withdraws?".

### What KidneyLoop does

- `kx match` picks disjoint cycles and chains with an exact integer program and records the plan in the graph as a MatchRun. Scoring comes from a policy JSON file (`policies/`), not hard-coded weights; one policy follows the OPTN KPD priority-points table where our data can express it.
- `--objective expected` plans for transplants that actually happen. Most planned KPD transplants fail (crossmatch and other failures, rates from Dickerson, Procaccia and Sandholm 2019): a cycle needs every transplant, a chain stops at its first failure. `kx simulate` replays both plans on the same sampled outcomes: on the committed pool the count plan realises 49.9 transplants per round and the expected plan 84.6 (+34.7, 95% CI +34.0 to +35.5).
- `kx why <patient>` explains a match or a miss from graph paths: blood-type candidates, the antigens that block donors, the cycles through the patient and which other exchange holds each blocking patient.
- `kx counterfactual <patient>` tries single changes (one antibody less, longer chains, 3-way cycles, the patient required) and verifies each by re-solving the whole pool inside a rolled-back transaction, reporting who would lose a kidney.
- `kx whatif --withdraw <donor>` repairs the plan the way programs do: intact exchanges stay, only freed and unmatched patients are re-planned. It reports who loses a kidney and who needs a new crossmatch, compares with a full re-plan (which would move 155 patients), and rolls back.
- `kx audit` compares a SHA-256 of the solver's inputs and policy, stored on every MatchRun, with the store as it is now; `why`, `whatif` and `counterfactual` warn when an answer may be stale.
- A web explorer runs the same Cypher in the browser on Relate's WebAssembly build and draws the cycles behind each answer. An MCP config lets any agent query the store read-only.

### How it uses Relate

- Directory store with the `json` codec, committed at `data/kidneyloop/`, so judges can read `graph.json` and `git diff` shows what each matching round decided.
- Blood-type rules (`ABO_CAN_GIVE_TO`) and antibody to antigen links are graph data; Cypher derives every `COMPATIBLE` edge and each patient's pool PRA (the share of pool donors their antibodies rule out).
- Multi-hop Cypher enumerates 2-way and 3-way cycles (38,332 on the committed pool) and walks the paths behind each explanation.
- Transactions with rollback power the what-if and the counterfactuals; read-only opens power `why`, `audit`, `simulate` and the MCP server.
- `relate serve --mcp stdio --read-only` and `@relate/relate-wasm` serve the same graph to agents and to the browser.

### What connects to what

Donor `PAIRED_WITH` Patient; Donor and Patient `HAS_BLOOD` BloodType, which `ABO_CAN_GIVE_TO` BloodType; Donor `HAS_ANTIGEN` Antigen and Patient `HAS_ANTIBODY` Antigen; Donor `COMPATIBLE` Patient (derived); MatchRun `SELECTED` Exchange, which `INCLUDES` donors and patients in order.

### Testing

478 tests, including a brute-force optimality check of the solver on 150 random pools, the expected objective checked against an exhaustive search, Cypher cycle enumeration checked against an independent search, reproducibility across row order and Python hash seeds, end-to-end runs of the real CLI and MCP server, and a mutation check that plants fourteen bugs and confirms the suite catches each. CI runs all of it on Linux.

### Data and safety

All patients and donors are synthetic, generated from a fixed seed. The compatibility rules are simplified, pool PRA stands in for CPRA, and the failure model assumes independent transplants; the simulation measures the gain if that model is right. This is a demonstration, not a clinical tool.

## Built with

relate, relate-db (Python), @relate/relate-wasm, Cypher, MCP, Python, scipy (HiGHS), numpy, typer, pytest, JavaScript, GitHub Actions

## Capabilities, third-party tools and AI tools used (required by the rules)

- Relate capabilities: embedded Python client, Cypher multi-hop patterns, transactions with rollback, read-only opens, directory store with the json codec, the relate CLI, the MCP server, the WebAssembly build.
- Third-party libraries: scipy and numpy (BSD), typer (MIT), pytest (MIT). `web/relate/` is the unmodified `@relate/relate-wasm` 1.1.0 package (Apache-2.0).
- AI tools: the project was planned, written and tested with Claude Code (Anthropic), working with the author. The author chose the idea and the design decisions and reviewed the result.

## Team

TODO: your name, and teammates if any.
