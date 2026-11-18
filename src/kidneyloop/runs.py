import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

import relate

from .exchanges import Exchange, find_arcs, find_cycles
from .solver import MatchInputs, Plan, patient_weight, solve

OBJECTIVE = "max transplants; tie-break 0.7 x pool PRA + 0.3 x wait share"
SOLVER = "HiGHS via scipy.optimize.milp"

PATIENTS = "MATCH (p:Patient) RETURN p.id AS id, p.pool_pra AS pool_pra, p.wait_days AS wait_days"
PAIRS = "MATCH (d:Donor)-[:PAIRED_WITH]->(p:Patient) RETURN d.id AS donor, p.id AS patient"

NEXT_SEQ = "OPTIONAL MATCH (r:MatchRun) RETURN coalesce(max(r.seq), 0) + 1 AS seq"
CREATE_RUN = """
CREATE (r:MatchRun {id: $id, seq: $seq, created_at: $created_at, objective: $objective, solver: $solver,
                    max_cycle: $max_cycle, max_chain: $max_chain, transplants: $transplants,
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
RUN_EXCHANGES = """
MATCH (:MatchRun {id: $run})-[:SELECTED]->(e:Exchange)-[i:INCLUDES]->(x)
WITH e, i.order AS step, i.role AS role, x.id AS who
ORDER BY e.id, step
WITH e, collect(CASE WHEN role = 'donor' THEN who END) AS donors,
        collect(CASE WHEN role = 'patient' THEN who END) AS patients
RETURN e.id AS id, e.kind AS kind, donors, patients
ORDER BY id
"""


@dataclass(frozen=True)
class RunSummary:
    id: str
    plan: Plan
    candidate_cycles: int
    candidate_arcs: int


Progress = Callable[[str], None]


def _quiet(_: str) -> None:
    pass


def read_inputs(conn: relate.Connection, max_cycle: int = 3, progress: Progress = _quiet) -> MatchInputs:
    patients = list(conn.execute(PATIENTS))
    max_wait = max((p["wait_days"] for p in patients), default=0)
    weight = {p["id"]: patient_weight(p["pool_pra"], p["wait_days"], max_wait, len(patients)) for p in patients}
    donor_of = {row["patient"]: row["donor"] for row in conn.execute(PAIRS)}
    started = time.perf_counter()
    cycles = tuple(find_cycles(conn, max_cycle))
    arcs = tuple(find_arcs(conn))
    progress(f"Cypher found {len(cycles):,} cycles and {len(arcs):,} arcs in {time.perf_counter() - started:.1f} s")
    return MatchInputs(cycles, arcs, donor_of, weight)


def solve_with_progress(inputs: MatchInputs, max_chain: int, progress: Progress = _quiet) -> Plan:
    progress("Solving the integer program (exact, HiGHS)...")
    started = time.perf_counter()
    plan = solve(inputs, max_chain)
    progress(f"Solved: {plan.transplants} transplants in {time.perf_counter() - started:.1f} s")
    return plan


def run_match(conn: relate.Connection, max_cycle: int = 3, max_chain: int = 4, progress: Progress = _quiet) -> RunSummary:
    """Solve the current pool and record the chosen exchanges as a new MatchRun.

    progress receives one line per stage, since a 300-pair round takes tens of seconds.
    """
    progress("Enumerating cycles in Cypher...")
    inputs = read_inputs(conn, max_cycle, progress)
    plan = solve_with_progress(inputs, max_chain, progress)
    seq = conn.execute(NEXT_SEQ).single()["seq"]
    run_id = f"R{seq}"
    conn.execute(
        CREATE_RUN,
        {
            "id": run_id,
            "seq": seq,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "objective": OBJECTIVE,
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
    return RunSummary(run_id, plan, len(inputs.cycles), len(inputs.arcs))


def latest_run_id(conn: relate.Connection) -> str | None:
    rows = list(conn.execute(LATEST_RUN))
    return rows[0]["id"] if rows else None


def run_exchanges(conn: relate.Connection, run_id: str) -> list[tuple[str, Exchange]]:
    return [
        (row["id"], Exchange(row["kind"], tuple(row["donors"]), tuple(row["patients"])))
        for row in conn.execute(RUN_EXCHANGES, {"run": run_id})
    ]
