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
| `MatchRun {id, seq, created_at, objective, objective_kind, policy, policy_sha256, input_sha256, solver, transplants, ...}` | `(Donor)-[:COMPATIBLE]->(Patient)`, derived in Cypher from the two rules above |
| `Exchange {id, kind, size}` | `(MatchRun)-[:SELECTED]->(Exchange)-[:INCLUDES {order, role}]->(Donor or Patient)` |

The blood-type and antibody rules are data in the graph, not code. Cypher derives from them every `COMPATIBLE` edge and each patient's `pool_pra`, the share of this pool's donors their antibodies rule out. Pool PRA plays the role cPRA plays in real programs, but it is not the clinical cPRA, which is computed against a reference donor population. A test checks the derived edges against an independent Python implementation of the same rules.

Relate capabilities used:

- **Embedded Python client** (`relate-db`). It builds the store, runs the matching queries, and runs the transactions.
- **Multi-hop Cypher patterns.** They enumerate 2-way and 3-way cycles, and explain a patient by walking blood-type and antibody paths.
- **Transactions with rollback.** `kx whatif` deletes a donor, repairs the plan and compares it with a full re-plan inside one transaction, then rolls it back. `kx counterfactual` removes one antibody, re-derives `COMPATIBLE` and re-solves inside a transaction, then rolls it back.
- **Read-only opens.** `kx why`, `kx audit`, `kx options` and the MCP server open the store read-only.
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
kx counterfactual P071      # single changes that would get P071 matched, each verified by a re-solve
kx audit                    # has the pool changed since the latest run was solved?
kx options                  # candidate cycles and arcs for a round
kx match                    # solve again and record a new MatchRun
kx simulate                 # replay the count plan and the failure-aware plan on sampled outcomes (read-only)
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

Patient and donor ids are case-insensitive. `kx match` and `kx whatif` print each stage and its time to stderr while they run. They took 30 to 60 seconds on the author's Windows laptop for the 300-pair pool. Most of that time goes to cycle enumeration in Cypher and the exact integer program. `kx why` answers in under a second. `kx counterfactual` re-solves the pool once per change it tries, up to six times, and took about 4 minutes for P071.

### Auditing a run

Every `kx match` stores an `input_sha256` on its `MatchRun`: a SHA-256 of what the solver read, namely each patient's id, `pool_pra`, `wait_days`, blood type and, once `kx cpra` has run, `cpra`, each donor's id, blood type, altruistic flag and paired patient, every `COMPATIBLE` arc, the run's cycle limit, chain limit, policy hash and objective, and, when exchanges are under way, the patients and donors they hold. Rows are sorted by id first, so the hash does not depend on the order Relate returns them or on Python's hash seed; tests check both. The run also stores `objective_kind` (`count` or `expected`) next to the readable `objective` text.

`kx audit [RUN]` (the latest run by default) recomputes the hash from the store as it is now and compares. `kx why`, `kx whatif` and `kx counterfactual` print a warning line first when the pool changed since the latest run, because their answer may then be stale. Output from a copy of the committed store after `kx match` recorded R2, and again after P071's `wait_days` was raised by 30:

```text
$ kx audit
R2, created 2026-11-18T16:43:31+00:00
  policy: default (sha256 7a0bfdc90249ebfacb163e3378bb71913d36ba6b98a7221cbe10e6066b7e9e00)
  objective: max transplants; tie-break from policy default
  solver: HiGHS via scipy.optimize.milp
  limits: cycles of up to 3, chains of up to 4
  stored input hash:  ce0fcf623b4e44c412e59b3192395cbb4df5fba017ee10683b5dd73bca0b1ce4
  current input hash: ce0fcf623b4e44c412e59b3192395cbb4df5fba017ee10683b5dd73bca0b1ce4
  inputs unchanged
$ kx audit R2
  ...
  stored input hash:  ce0fcf623b4e44c412e59b3192395cbb4df5fba017ee10683b5dd73bca0b1ce4
  current input hash: e227987548a4a6a02ed1ca6a77b2174dd31f61d33f7a735d985215b1cd6b1e99
  inputs changed since this run
$ kx why P071
Warning: the pool changed since R2 was recorded, so this answer may be stale (see `kx audit R2`).
P071: blood type O; antibodies rule out 86% of this pool's donors (pool PRA)
...
```

The committed run R1 carries its hash, so `kx audit` on a fresh clone prints `inputs unchanged`. A run recorded before input hashing gets `cannot tell whether inputs changed since this run` instead. The hash covers solver inputs only. A changed antibody or antigen that has not been re-derived into `COMPATIBLE` edges does not change it.

### Counterfactuals

`kx counterfactual P071` asks which single change would get an unmatched patient into the plan, and checks each candidate by re-solving the whole pool with the latest run's cycle and chain limits, policy and objective. The candidates are bounded:

1. Without one of the patient's antibodies, for the three antigens that block the most blood-compatible donors (as in `kx why`). The `HAS_ANTIBODY` edge is deleted, every `COMPATIBLE` edge and `pool_pra` is re-derived with the store's own Cypher rules, and the pool is re-solved, all inside a transaction that is rolled back. If dropping the antibody opens no donor, because each donor it blocks carries another antigen the patient reacts to, that change is reported and not re-solved.
2. Chains one transplant longer than the run allowed.
3. 3-way cycles, if the run allowed only 2-way ones.
4. The patient required: serving them is made worth more than any number of other transplants, so the solver includes them whenever any exchange can. The transplant delta is the price of serving them, and the lost list names who pays it.

For each change it reports the transplant count against the recorded run, every patient who would lose their kidney, and how many would move to a different donor. On a copy of the committed store (about 4 minutes):

```text
P071 is not matched in R1 (count objective, policy default, cycles of up to 3, chains of up to 4: 193 transplants).
Single changes, each checked by re-solving the whole pool:
  without the antibody to A2 (it blocks 19 blood-compatible donors): opens 5 donors; P071 still unmatched; 193 -> 193 transplants (+0); lose a kidney: none; 142 moved to a different donor
  without the antibody to DR14 (it blocks 19 blood-compatible donors): opens 6 donors; P071 still unmatched; 193 -> 193 transplants (+0); lose a kidney: none; 146 moved to a different donor
  without the antibody to DR12 (it blocks 16 blood-compatible donors): opens 4 donors; P071 still unmatched; 193 -> 193 transplants (+0); lose a kidney: none; 151 moved to a different donor
  chains of up to 5 instead of 4: P071 still unmatched; 193 -> 193 transplants (+0); lose a kidney: none; 159 moved to a different donor
  P071 required in the plan: P071 matched; 193 -> 193 transplants (+0); lose a kidney: P167; 147 moved to a different donor
First change that gets P071 matched: P071 required in the plan; P167 would lose a kidney.
(antibody changes rolled back; the store is unchanged)
```

So for P071 no clinical change helps: each antibody change opens four to six donors, but the optimal plan still leaves P071 out. Requiring P071 shows why: it costs no transplants, only P167's. Two plans with 193 transplants exist, and the tie-break (pool PRA and wait) picked the one that serves P167. A coordinator who wants P071 instead now sees exactly who pays for it. Tests on small pools built so the answer is known check the other outcomes: a dropped antibody that matches the patient and names the patient who loses a kidney, a longer chain that reaches the patient, 3-way cycles that help a 2-cycle run, a required patient who bumps another, and a case where no single change helps.

To rebuild the store from scratch, install the relate CLI ([install guide](https://relatedb.ai/docs/get-started/install)) and run `kx build` (seed 42, 300 pairs, 10 altruistic donors), then `kx match`. `kx build` with the same seed writes a byte-identical `graph.json`. `kx match` then adds a `MatchRun` whose `created_at` is the time of the run, and it picks the same exchanges every time for the same pool: when several plans tie, the solver's input order is fixed, so the choice does not depend on row order or Python's hash seed. `kx build` refuses to replace a folder that is not a Relate store.

### Importing a published benchmark pool

`kx import-preflib` reads a kidney instance from [PrefLib dataset 00036](https://preflib.github.io/PrefLib-Jekyll/dataset/00036) (synthetic pools from Dickerson, Procaccia and Sandholm, AAMAS 2012) into a new store. The files give the compatibility arcs, not HLA typing, so the store keeps the dataset's arcs as `COMPATIBLE` edges instead of deriving them, and each patient's pool PRA counts the blood-compatible donors the dataset rules out. The dataset's own PRA is kept as `source_pra`.

```bash
kx import-preflib 00036-00000111.dat --store data/preflib-111
kx match --store data/preflib-111
```

`python scripts/check_preflib.py 111 200 --match` reproduces the figures in this paragraph. It downloads each instance from a pinned PrefLib-Data commit into `data/cache/preflib/`, counts cycles with KidneyLoop's Cypher and with a separate Python search over the dataset's arcs, and exits 1 if the counts differ. The PrefLib files are not committed and CI does not download them, so the tests use a small synthetic instance in the same format. On instance 111 (128 pairs, 4,108 arcs) Cypher finds 8,953 cycles, the same count as the script's separate search, and `kx match` picks 83 transplants. The importer checks every arc against the blood-type rules, the declared edge count and each pair's out-degree, and it refuses what it cannot import faithfully: files with altruistic donors (PrefLib marks them as faulty, and the one checked here, instance 50, has arcs that break the blood-type rules) and arc weights other than 1. Instance 200 (512 pairs, 63,154 arcs) has 535,344 cycles of up to 3, again the same count as an independent search. Cypher enumerates them in about 3.5 minutes, and `kx match`, which enumerates them again before HiGHS solves, picks 312 transplants in about 8 minutes (one run of the script on a Windows laptop with nothing else running). Cycles are fetched 32 first patients at a time; in one query this pool's result passed the engine's 256 MB value limit.

### Web explorer

```bash
python -m http.server 8000
```

Add `?snapshot=<path>` to open another store's `graph.json`, for example `web/?snapshot=../data/preflib-111/graph.json`.

Open <http://localhost:8000/web/>. The page loads `data/kidneyloop/graph.json` into Relate's WebAssembly build in a worker. It shows the latest run as cycle and chain diagrams you can filter and open, and the match rate of each blood type and sensitisation band against the round's rate; selecting a group lists its unmatched patients. On the committed pool all 107 unmatched patients have blood type O. It explains any patient with the same text as `kx why`, plus a drawing of the cycles that could serve them, with the patient highlighted and the blocking patients marked. It can withdraw a donor in the tab's copy, and it has a Cypher console. The browser has no solver, so the page shows what breaks and points to `kx whatif` for the re-plan.

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
3. The integer program is solved exactly with HiGHS through `scipy.optimize.milp`. Each transplant is worth 1, plus a tie-break of at most 1/(patients+1) set by a policy file (the default favours high pool PRA and long waits). Summed over all patients, the tie-break stays below one transplant, so it never costs one.
4. Cycles are capped at 3 pairs and chains at 4 transplants. Programs cap cycle length because every transplant in a cycle needs simultaneous surgery, as surveyed in [this 2025 review](https://arxiv.org/pdf/2504.04091). The chain cap of 4 is this project's choice.

The compatibility rules are simplified: ABO blood type plus a synthetic antibody to antigen match over 32 HLA antigen names. The blood-type frequencies and antibody counts in the generator are illustrative, not population statistics.

## Policies and failure-aware matching

Real paired-donation programs see most planned matches fall through. In the OPTN/UNOS pilot, only 8% of the match offers studied led to a transplant, and 32.6% of refused offers were refused for crossmatch reasons ([Leishman et al., ATC 2015](https://atcmeetingabstracts.com/?p=466)). Plain `kx match` assumes every compatible arc succeeds. Two options change that.

**Policy files.** A policy is a JSON file under [`policies/`](policies). It sets the tie-break score and an edge success model. `kx match --policy <file>` uses it (default [`policies/default.json`](policies/default.json)), and the `MatchRun` records the policy name and a SHA-256 of the file, with line endings normalised to LF. Each patient's tie-break score is divided by the largest score the policy can give in this pool, so it lies in [0, 1], and the total over all patients stays below one transplant for every policy. A test checks this for each shipped file.

- [`policies/default.json`](policies/default.json) reproduces the original scoring exactly (0.7 x pool PRA + 0.3 x wait share). The 0.7 and 0.3 are this project's choice, not from a published scheme. With it, the solver picks the same 66 exchanges as the committed run R1.
- [`policies/optn-kpd-2019-inspired.json`](policies/optn-kpd-2019-inspired.json) is **inspired by, not a certified implementation of**, [OPTN Policy 13.7.F, Table 13-2: OPTN KPD Prioritization Points](https://optn.transplant.hrsa.gov/media/3239/20191011_kidney_kpd_priority_points.pdf), effective 8 October 2019 (read from the [Internet Archive copy](http://web.archive.org/web/20250527133532/https://optn.transplant.hrsa.gov/media/3239/20191011_kidney_kpd_priority_points.pdf)). It uses the rows this pool can express: 0.07 points per day waiting, the CPRA sliding scale from 0 to 2000 points, 125 more points at CPRA 80% or above, candidate ABO points and paired-donor ABO points. Pool PRA, truncated to a whole percent, stands in for CPRA, and `wait_days` stands in for days registered in KPD. The file lists the rows it leaves out, which need data the pool lacks or depend on the donor-patient pair. OPTN picks the exchanges with the most total points, with 100 base points per match. KidneyLoop uses the points only to break ties between plans with the same number of transplants.

**Edge success model.** Both policies use the same model, and every number in it comes from [Dickerson, Procaccia and Sandholm (2019), Failure-Aware Kidney Exchange](https://par.nsf.gov/servlets/purl/10120603), section 8.2. They report the crossmatch failure rates of Ashlagi et al. (2011) by CPRA band (0.05 for 0-24, 0.2 for 25-49, 0.35 for 50-74, 0.5 above 75) and add an exogenous failure rate of 0.08. So a planned transplant succeeds with probability 0.87, 0.72, 0.57 or 0.42, by the recipient's band. These are assumptions, labelled in the files: pool PRA stands in for CPRA; the top band starts at 75 because the source reports the band below as 50-74; transplants succeed or fail independently; and a transplant's chance depends only on its recipient.

**Expected objective.** `kx match --objective expected` maximises expected transplants instead of planned ones, with the same paper's model. A cycle happens only if all its transplants do, so its value is multiplied by the product of their chances. A chain runs in order and stops at its first failure, so the transplant at position k is worth the chance that the first k all succeed. The position-indexed chain variables cannot see which patients came earlier, so the solver approximates. The arc at position k fixes the patient at k and, through its donor, the patient at k-1; the k-2 positions before those take the mean success over paired patients. This is exact for chains of up to two transplants and for any pool with one success rate, and tests check both cases against a brute-force search for the exact expected optimum. On longer chains with mixed rates, a test requires at least 95% of that optimum; on 300 random small pools the measured worst case was 96.1%, and 229 of the 237 pools with any exchange were exact. In this mode the tie-break still totals below one transplant, but two plans' expected transplants can differ by less than one, so the tie-break can outweigh a small expected difference. `--objective count`, the default, is the original behaviour.

**Measured, not asserted.** `kx simulate --trials N --seed S` opens the store read-only. It solves the pool once per objective, then samples, for each trial, one outcome for every planned transplant; a transplant both plans use gets one shared draw. It realises cycles all-or-nothing and chains up to the first failure, and reports both plans on the same outcomes. On the committed store (built with seed 42, 300 pairs), each run takes about 40 seconds:

```bash
kx simulate --trials 1000 --seed 42
```

```text
1000 trials, seed 42, policy default; same sampled outcomes for both plans
plan            planned  expected  realized mean [95% CI]
count               193      49.9  49.9 [49.3, 50.4]
expected            185      84.5  84.6 [84.1, 85.1]
expected - count, paired by trial: +34.7 [+34.0, +35.5]
Realized transplants by pool PRA band, mean per trial:
  0-24%    (123 patients): count 15.7, expected 45.0
  25-49%   (50 patients): count 10.1, expected 13.5
  50-74%   (48 patients): count 9.7, expected 12.9
  75-100%  (79 patients): count 14.3, expected 13.3
```

```bash
kx simulate --trials 1000 --seed 42 --policy policies/optn-kpd-2019-inspired.json
```

```text
1000 trials, seed 42, policy optn-kpd-2019-inspired; same sampled outcomes for both plans
plan            planned  expected  realized mean [95% CI]
count               193      59.3  59.3 [58.7, 59.9]
expected            185      84.5  84.8 [84.3, 85.3]
expected - count, paired by trial: +25.5 [+24.7, +26.2]
Realized transplants by pool PRA band, mean per trial:
  0-24%    (123 patients): count 24.2, expected 45.0
  25-49%   (50 patients): count 11.3, expected 13.5
  50-74%   (48 patients): count 8.9, expected 12.9
  75-100%  (79 patients): count 15.0, expected 13.4
```

What this shows, and what it does not:

- The failure-aware plan plans 8 fewer transplants but realises about 35 more per round under the default policy (84.6 against 49.9), because it favours 2-way cycles and short chains, which survive more often.
- It does **not** help the most sensitized patients. In the 75-100% pool PRA band it realises 13.3 transplants per round against 14.3 for the count plan, and 13.4 against 15.0 under the OPTN-inspired policy. The other three bands gain.
- The OPTN-inspired tie-break alone raises the count plan from 49.9 to 59.3 realised transplants. Plans that tie on 193 planned transplants differ a lot in how many survive.
- The simulation draws outcomes from the same success model the expected plan optimises, so it measures the gain if that model is right. It does not test failure rates that differ from the model. The model is synthetic: pool PRA is not CPRA, and the cited rates come from other pools.
- `kx whatif` still re-plans with the default policy and the count objective. `kx counterfactual` uses the run's own policy and objective.

## After the match: tracking each exchange

A plan is only an offer. `kx status` records what happens next, one exchange at a time, and the next `kx match` plans around it.

```bash
kx status                                          # every exchange of the latest run, with its status
kx status R1-X5 --set accepted --note "both centres agreed"
kx status R1-X5 --set crossmatch_failed            # prints who goes back into the pool
```

| From | Can move to |
|---|---|
| proposed (a new exchange) | accepted, declined, cancelled |
| accepted | crossmatch_passed, crossmatch_failed, cancelled |
| crossmatch_passed | scheduled, cancelled |
| scheduled | transplanted, cancelled |
| transplanted, declined, crossmatch_failed, cancelled | nothing; they are final |

Each change sets `e.status` and appends a `StatusEvent {seq, previous, status, at, note}` through `HAD_STATUS`, so the history stays in the graph. Repeating the current status records nothing, and an illegal move names the moves allowed. Only the latest run's proposals can be accepted: a later run plans the same pool again, so an earlier offer's people may already be in its plan, and accepting both would promise them twice. An earlier run's exchange that was already accepted keeps moving through its steps. Exchanges that are accepted, crossmatch_passed, scheduled or transplanted keep their patients and donors out of the next round, including each patient's own donor; declined, failed and cancelled ones release them. The next run's input hash records who was held, while a run's own audit ignores statuses set on its own exchanges, so accepting R1-X5 does not make R1 look stale. A chain's last donor leaves with their patient rather than starting a new chain, which real programs often do.

## CPRA from a reference population

Pool PRA counts the donors in this pool that a patient's antibodies rule out. Clinical programs use CPRA, the share of a reference donor population they rule out. `kx cpra` computes it from NMDP haplotype frequencies and stores it as `p.cpra`:

```bash
kx cpra                      # population SCSEAI (Other Southeast Asian), downloads about 40 MB once into data/cache/
kx cpra --population EURCAU  # any population code in the NMDP file
kx match --policy policies/default-cpra.json
```

- Formula. The OPTN policy notice defines CPRA as the sum over donor genotypes of genotype frequency times whether the genotype carries an unacceptable antigen. With haplotype frequencies and Hardy-Weinberg genotypes that sum equals 1 - (sum of frequencies of haplotypes carrying none of them)^2, which is what `kx cpra` computes; a test checks the two forms agree on random tables. Only HLA-A, B and DR count, because those are the loci KidneyLoop types. Blood type stays with the ABO rules.
- Data. Frequencies come from the [NMDP 7-locus haplotype frequencies on Zenodo](https://zenodo.org/records/4474442) (CC BY-NC-ND 4.0), checked against the md5 Zenodo publishes. Alleles map to serologic antigens with [IPD-IMGT/HLA rel_dna_ser.txt](https://github.com/ANHIG/IMGTHLA) release 3.60.0 (CC BY-ND). Release 3.66.0 writes values such as 4001 in the serology column where 3.60.0 gives the antigen (B60), so the release is pinned. Neither licence allows redistributing modified data, so both files are downloaded at run time and `data/cache/` is not committed.
- Population. The default is SCSEAI, NMDP's "Other Southeast Asian" group. NMDP has no Thai group, and this is the closest one by region, not one validated against Thai typing data. OPTN weights its US populations by their share of deceased donors; KidneyLoop uses one population at a time.
- Limits. An allele with no single serologic antigen is left out of its haplotype, which keeps its other antigens, so CPRA can only be underestimated; in SCSEAI that is 0.05% of the haplotype frequency. Antibodies to associated antigens (B75, a split of B15, for example) only match donors whose allele maps to that same antigen.
- Matching. `policies/default-cpra.json` is the default policy with `"sensitisation": "cpra"`, so the tie-break and the success model read CPRA. Without that line a policy reads pool PRA, and a store without CPRA keeps its recorded input hashes. `kx why` adds the patient's CPRA once it is set.

On the committed pool, in a manual run on 2026-11-19 (the NMDP file is downloaded, so the tests do not repeat it), the most sensitised patients score 94 to 98% CPRA against SCSEAI where their pool PRA is 99 to 100%.

## Testing and QA

```bash
pytest -m "not qa_full"       # 561 tests, about 1 min
pytest -m qa_full             # the committed 300-pair store, about 2 min
python scripts/mutation_check.py
```

- **Unit tests.** Blood-type and antibody rules, pool PRA, cycle uniqueness, the recorded run reading back, explanations, what-if, the input hash (unchanged pool, a changed edge or wait, load order, hash seed) and counterfactuals on pools whose answer is known by construction, including one where no single change helps.
- **Solver against brute force.** On 150 random small pools the solver's plan obeys every rule and equals the optimum of an independent exhaustive search. The expected objective is checked against an exhaustive search for the exact expected optimum.
- **Policies and simulation.** The default policy reproduces the original weights bit for bit, every shipped policy keeps the tie-break below one transplant, a malformed policy names the bad field, and simulated cycles and chains succeed and stop as the model says.
- **Cypher against reference.** Cycles found by Cypher equal cycles found by an independent Python search on generated pools.
- **End-to-end QA** (`tests/qa`). The real CLI runs on a fresh store. Tests cover reproducible builds, the relate CLI reading what Python wrote, a recorded run that obeys every rule with no unused cycle or altruist left, read-only commands and what-if leaving the store files unchanged, error exits, the MCP server, and the web page running the same Cypher as the CLI.
- **Mutation check.** `scripts/mutation_check.py` plants 21 known bugs one at a time, including a superseded proposal being accepted, CPRA that counts one haplotype instead of two, imported arcs that skip the blood-type check, a plan that changes with row order or Python's hash seed, an expected objective that ignores chain position, a simulated chain that continues past a failure, an input hash that ignores `COMPATIBLE` edges, and a counterfactual that commits instead of rolling back, and confirms the suite fails on each.

[docs/QA.md](docs/QA.md) lists the browser checks done by hand on the web explorer.

## Tools used

- **Relate 1.1.0.** The Python client `relate-db`, the CLI, the MCP server and `@relate/relate-wasm`.
- **Python libraries.** `scipy` (HiGHS MILP), `numpy`, `typer`, `pytest`.
- **AI tools.** This project was planned, written and tested with Claude Code (Anthropic), working with the author. The author chose the idea and the design decisions and reviewed the result.

## License

MIT, see [LICENSE](LICENSE). `web/relate/` is Apache-2.0, from the Relate project.
