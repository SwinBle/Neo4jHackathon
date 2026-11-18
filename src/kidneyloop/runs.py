import hashlib
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

import relate

from .exchanges import Exchange, find_arcs, find_cycles
from .policy import DEFAULT_POLICY_FILE, PatientFacts, Policy, PoolFacts, load_policy
from .solver import MatchInputs, Objective, Plan, expected_transplants, patient_weight, solve

OBJECTIVE_TEXT: dict[Objective, str] = {
    "count": "max transplants",
    "expected": "max expected transplants (a cycle needs every transplant; a chain stops at its first failure)",
}
SOLVER = "HiGHS via scipy.optimize.milp"

PATIENTS = """
MATCH (p:Patient)-[:HAS_BLOOD]->(b:BloodType)
OPTIONAL MATCH (d:Donor)-[:PAIRED_WITH]->(p)
OPTIONAL MATCH (d)-[:HAS_BLOOD]->(db:BloodType)
RETURN p.id AS id, p.pool_pra AS pool_pra, p.wait_days AS wait_days, b.name AS blood, db.name AS donor_blood
"""
PAIRS = "MATCH (d:Donor)-[:PAIRED_WITH]->(p:Patient) RETURN d.id AS donor, p.id AS patient"

NEXT_SEQ = "OPTIONAL MATCH (r:MatchRun) RETURN coalesce(max(r.seq), 0) + 1 AS seq"
CREATE_RUN = """
CREATE (r:MatchRun {id: $id, seq: $seq, created_at: $created_at, objective: $objective, solver: $solver,
                    objective_kind: $objective_kind, policy: $policy, policy_sha256: $policy_sha256,
                    input_sha256: $input_sha256,
                    max_cycle: $max_cycle, max_chain: $max_chain, transplants: $transplants,
                    expected_transplants: $expected_transplants,
                    candidate_cycles: $candidate_cycles, candidate_arcs: $candidate_arcs})
WITH r
UNWIND $exchanges AS ex
CREATE (r)-[:SELECTED]->(e:Exchange {id: ex.id, kind: ex.kind, size: ex.size})
WITH e, ex
UNWIND range(0, size(ex.patients) - 1) AS i
MATCH (d:Donor {id: ex.donors[i]}), (p:Patient {id: ex.patients[i]})
CREATE (e)-[:INCLUDES {order: i + 1, role: 'donor'}]->(d),
       (e)-[:INCLUDES {order: i + 1, role: 'patient'}]->(p)
"""

LATEST_RUN = "MATCH (r:MatchRun) RETURN r.id AS id ORDER BY r.seq DESC LIMIT 1"
RUN = "MATCH (r:MatchRun {id: $run}) RETURN properties(r) AS run"
RUN_EXCHANGES = """
MATCH (:MatchRun {id: $run})-[:SELECTED]->(e:Exchange)-[i:INCLUDES]->(x)
WITH e, i.order AS step, i.role AS role, x.id AS who
ORDER BY e.id, step
WITH e, collect(CASE WHEN role = 'donor' THEN who END) AS donors,
        collect(CASE WHEN role = 'patient' THEN who END) AS patients
RETURN e.id AS id, e.kind AS kind, donors, patients
ORDER BY id
"""

HASH_PATIENTS = """
MATCH (p:Patient)-[:HAS_BLOOD]->(b:BloodType)
RETURN p.id AS id, p.pool_pra AS pool_pra, p.wait_days AS wait_days, b.name AS blood
"""
HASH_DONORS = """
MATCH (d:Donor)-[:HAS_BLOOD]->(b:BloodType)
OPTIONAL MATCH (d)-[:PAIRED_WITH]->(p:Patient)
RETURN d.id AS id, b.name AS blood, d.altruistic AS altruistic, p.id AS paired_with
"""
HASH_ARCS = "MATCH (d:Donor)-[:COMPATIBLE]->(p:Patient) RETURN d.id AS donor, p.id AS patient"


def input_sha256(
    conn: relate.Connection, max_cycle: int, max_chain: int, policy_sha256: str | None, objective: Objective
) -> str:
    """A hash of everything the solver reads for a run, so a later reader can tell whether the pool changed.

    Rows are sorted by id before hashing, so the hash does not depend on the order
    the engine returns them in.
    """
    snapshot = {
        "patients": sorted([r["id"], r["pool_pra"], r["wait_days"], r["blood"]] for r in conn.execute(HASH_PATIENTS)),
        "donors": sorted([r["id"], r["blood"], r["altruistic"], r["paired_with"]] for r in conn.execute(HASH_DONORS)),
        "compatible": sorted([r["donor"], r["patient"]] for r in conn.execute(HASH_ARCS)),
        "max_cycle": max_cycle,
        "max_chain": max_chain,
        "policy_sha256": policy_sha256,
        "objective": objective,
    }
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class RunSummary:
    id: str
    plan: Plan
    candidate_cycles: int
    candidate_arcs: int
    expected_transplants: float


Progress = Callable[[str], None]


def _quiet(_: str) -> None:
    pass


def read_inputs(
    conn: relate.Connection, max_cycle: int = 3, progress: Progress = _quiet, policy: Policy | None = None
) -> MatchInputs:
    policy = policy or load_policy()
    patients = list(conn.execute(PATIENTS))
    pool = PoolFacts(max_wait=max((p["wait_days"] for p in patients), default=0))
    weight = {
        p["id"]: patient_weight(
            policy.tie(PatientFacts(p["pool_pra"], p["wait_days"], p["blood"], p["donor_blood"]), pool), len(patients)
        )
        for p in patients
    }
    success = {p["id"]: policy.success.probability(p["pool_pra"]) for p in patients}
    donor_of = {row["patient"]: row["donor"] for row in conn.execute(PAIRS)}
    started = time.perf_counter()
    cycles = tuple(find_cycles(conn, max_cycle))
    arcs = tuple(find_arcs(conn))
    progress(f"Cypher found {len(cycles):,} cycles and {len(arcs):,} arcs in {time.perf_counter() - started:.1f} s")
    return MatchInputs(cycles, arcs, donor_of, weight, success)


def solve_with_progress(
    inputs: MatchInputs, max_chain: int, progress: Progress = _quiet, objective: Objective = "count"
) -> Plan:
    progress(f"Solving the integer program for {objective} (exact, HiGHS)...")
    started = time.perf_counter()
    plan = solve(inputs, max_chain, objective)
    progress(f"Solved: {plan.transplants} transplants in {time.perf_counter() - started:.1f} s")
    return plan


def run_match(
    conn: relate.Connection,
    max_cycle: int = 3,
    max_chain: int = 4,
    progress: Progress = _quiet,
    policy: Policy | None = None,
    objective: Objective = "count",
) -> RunSummary:
    """Solve the current pool and record the chosen exchanges as a new MatchRun.

    progress receives one line per stage, since a 300-pair round takes tens of seconds.
    """
    policy = policy or load_policy()
    progress("Enumerating cycles in Cypher...")
    inputs = read_inputs(conn, max_cycle, progress, policy)
    plan = solve_with_progress(inputs, max_chain, progress, objective)
    expected = expected_transplants(plan, inputs.success)
    digest = input_sha256(conn, max_cycle, max_chain, policy.sha256, objective)
    seq = conn.execute(NEXT_SEQ).single()["seq"]
    run_id = f"R{seq}"
    conn.execute(
        CREATE_RUN,
        {
            "id": run_id,
            "seq": seq,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "objective": f"{OBJECTIVE_TEXT[objective]}; tie-break from policy {policy.name}",
            "objective_kind": objective,
            "input_sha256": digest,
            "policy": policy.name,
            "policy_sha256": policy.sha256,
            "expected_transplants": expected,
            "solver": SOLVER,
            "max_cycle": max_cycle,
            "max_chain": max_chain,
            "transplants": plan.transplants,
            "candidate_cycles": len(inputs.cycles),
            "candidate_arcs": len(inputs.arcs),
            "exchanges": [
                {"id": f"{run_id}-X{i}", "kind": e.kind, "size": e.size, "donors": list(e.donors), "patients": list(e.patients)}
                for i, e in enumerate(plan.exchanges, start=1)
            ],
        },
    )
    return RunSummary(run_id, plan, len(inputs.cycles), len(inputs.arcs), expected)


def latest_run_id(conn: relate.Connection) -> str | None:
    rows = list(conn.execute(LATEST_RUN))
    return rows[0]["id"] if rows else None


def run_exchanges(conn: relate.Connection, run_id: str) -> list[tuple[str, Exchange]]:
    return [
        (row["id"], Exchange(row["kind"], tuple(row["donors"]), tuple(row["patients"])))
        for row in conn.execute(RUN_EXCHANGES, {"run": run_id})
    ]


def run_record(conn: relate.Connection, run_id: str) -> dict:
    rows = list(conn.execute(RUN, {"run": run_id}))
    if not rows:
        raise KeyError(f"no run {run_id}")
    return dict(rows[0]["run"])


def run_objective(run: dict) -> Objective:
    """The run's objective; runs recorded before objective_kind existed are read from the objective text."""
    if run.get("objective_kind"):
        return run["objective_kind"]
    text = run["objective"].split(";")[0]
    return next(kind for kind, words in OBJECTIVE_TEXT.items() if words == text)


def run_policy(run: dict) -> Policy:
    """The policy file under policies/ whose hash the run recorded.

    Runs recorded before policies existed used the weights policies/default.json reproduces.
    """
    if run.get("policy_sha256") is None:
        return load_policy()
    for path in sorted(DEFAULT_POLICY_FILE.parent.glob("*.json")):
        policy = load_policy(path)
        if policy.sha256 == run["policy_sha256"]:
            return policy
    raise LookupError(f"policy {run['policy']} of {run['id']} (sha256 {run['policy_sha256'][:12]}) is not under policies/")


@dataclass(frozen=True)
class InputCheck:
    run: str
    stored: str | None
    current: str

    @property
    def unchanged(self) -> bool:
        return self.stored == self.current

    def warning(self) -> str | None:
        if self.stored is None:
            return f"Note: {self.run} was recorded without an input hash, so a change to the pool since cannot be detected."
        if not self.unchanged:
            return (
                f"Warning: the pool changed since {self.run} was recorded, so this answer may be stale "
                f"(see `kx audit {self.run}`)."
            )
        return None


def check_inputs(conn: relate.Connection, run_id: str) -> InputCheck:
    """Recompute the run's input hash from the store as it is now and pair it with the stored one."""
    run = run_record(conn, run_id)
    current = input_sha256(conn, run["max_cycle"], run["max_chain"], run.get("policy_sha256"), run_objective(run))
    return InputCheck(run_id, run.get("input_sha256"), current)
