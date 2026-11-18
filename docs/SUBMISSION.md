# Devpost submission text

Copy each field into the form at <https://relate.devpost.com/>. Fields marked TODO need something only you can provide.

## Project name

KidneyLoop

## Elevator pitch

Kidney paired-donation cycles and chains on a Relate graph, with the graph path behind every match and every miss.

## Repository

https://github.com/SwinBle/Neo4jHackathon (public, topics `relate` and `relate-neo4j-hackathon`)

## Demo video

TODO: your YouTube or Vimeo link (public or unlisted).

## About the project

### The problem

Many people who need a kidney have a relative or friend willing to donate, but the two are incompatible: the blood types don't fit, or the patient has antibodies against the donor's tissue type. Paired-donation programs pool these pairs and look for exchanges. Pair A's donor gives to patient B while B's donor gives to A (a cycle), or an altruistic donor starts a chain through several pairs. A coordinator needs the best plan, a reason for every patient left out, and a safe way to ask "what if this donor withdraws?".

### What KidneyLoop does

- `kx match` picks disjoint cycles and chains that maximise transplants, favouring hard-to-match patients on ties, and records the plan in the graph as a MatchRun.
- `kx why <patient>` explains a match or a miss from graph paths: blood-type candidates, the antigens that block donors, the cycles through the patient and which other exchange holds each blocking patient.
- `kx whatif --withdraw <donor>` deletes the donor and their patient inside a transaction, re-plans, reports what breaks, and rolls back.
- A web explorer runs the same Cypher in the browser on Relate's WebAssembly build and draws the cycles behind each answer.
- An MCP config lets any agent query the store read-only.

### How it uses Relate

- Directory store with the `json` codec, committed at `data/kidneyloop/`, so judges can read `graph.json` and `git diff` shows what each matching round decided.
- Blood-type rules (`ABO_CAN_GIVE_TO`) and antibody to antigen links are graph data; Cypher derives every `COMPATIBLE` edge and each patient's cPRA.
- Multi-hop Cypher enumerates 2-way and 3-way cycles (38,332 on the committed pool) and walks the paths behind each explanation.
- Transactions with rollback power the what-if; read-only opens power `why` and the MCP server.
- `relate serve --mcp stdio --read-only` and `@relate/relate-wasm` serve the same graph to agents and to the browser.

### What connects to what

Donor `PAIRED_WITH` Patient; Donor and Patient `HAS_BLOOD` BloodType, which `ABO_CAN_GIVE_TO` BloodType; Donor `HAS_ANTIGEN` Antigen and Patient `HAS_ANTIBODY` Antigen; Donor `COMPATIBLE` Patient (derived); MatchRun `SELECTED` Exchange, which `INCLUDES` donors and patients in order.

### Testing

208 tests, including a brute-force optimality check of the solver on 150 random pools, Cypher cycle enumeration checked against an independent search, end-to-end runs of the real CLI and MCP server, and a mutation check that plants six bugs and confirms the suite catches each. CI runs all of it on Linux.

### Data and safety

All patients and donors are synthetic, generated from a fixed seed. The compatibility rules are simplified. This is a demonstration, not a clinical tool.

## Built with

relate, relate-db (Python), @relate/relate-wasm, Cypher, MCP, Python, scipy (HiGHS), numpy, typer, pytest, JavaScript, GitHub Actions

## Capabilities, third-party tools and AI tools used (required by the rules)

- Relate capabilities: embedded Python client, Cypher multi-hop patterns, transactions with rollback, read-only opens, directory store with the json codec, the relate CLI, the MCP server, the WebAssembly build.
- Third-party libraries: scipy and numpy (BSD), typer (MIT), pytest (MIT). `web/relate/` is the unmodified `@relate/relate-wasm` 1.1.0 package (Apache-2.0).
- AI tools: the project was planned, written and tested with Claude Code (Anthropic), working with the author. The author chose the idea and the design decisions and reviewed the result.

## Team

TODO: your name, and teammates if any.
