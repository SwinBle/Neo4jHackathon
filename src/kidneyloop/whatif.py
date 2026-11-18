from dataclasses import dataclass

import relate

from .exchanges import Exchange
from .runs import latest_run_id, read_inputs, run_exchanges
from .solver import MatchInputs, Plan, solve

# A paired donor who withdraws takes their patient out of the exchange with them:
# the patient no longer brings a kidney to trade.
WITHDRAW = """
MATCH (d:Donor {id: $id})
OPTIONAL MATCH (d)-[:PAIRED_WITH]->(p:Patient)
WITH d, p, p.id AS leaving
DETACH DELETE d, p
RETURN leaving
"""
DONOR_EXISTS = "MATCH (d:Donor {id: $id}) RETURN count(d) AS n"
RUN_LIMITS = "MATCH (r:MatchRun {id: $run}) RETURN r.max_cycle AS max_cycle, r.max_chain AS max_chain"


@dataclass(frozen=True)
class WhatIf:
    donor: str
    run: str
    leaving: str | None
    broken: tuple[tuple[str, Exchange], ...]
    before: frozenset[str]
    replan: Plan
    replan_inputs: MatchInputs

    @property
    def stranded(self) -> tuple[str, ...]:
        """Patients of the current plan who were waiting on this donor or a donor after it.

        A cycle needs every donor, so all its patients are stranded. A chain still
        serves the patients before the withdrawn donor's position.
        """
        out: list[str] = []
        for _, e in self.broken:
            out.extend(e.patients if e.kind == "cycle" else e.patients[e.donors.index(self.donor):])
        return tuple(p for p in out if p != self.leaving)

    @property
    def after(self) -> frozenset[str]:
        return self.replan.matched_patients

    @property
    def lost(self) -> frozenset[str]:
        return self.before - self.after - {self.leaving}

    @property
    def gained(self) -> frozenset[str]:
        return self.after - self.before


def withdraw_donor(conn: relate.Connection, donor_id: str) -> WhatIf:
    """Re-plan the latest run as if the donor had withdrawn, then roll the withdrawal back.

    The re-plan uses the latest run's cycle and chain limits. The deletion and the
    re-plan happen inside one transaction that is always rolled back, so the store
    is unchanged afterwards.
    """
    if conn.execute(DONOR_EXISTS, {"id": donor_id}).single()["n"] == 0:
        raise KeyError(f"no donor {donor_id}")
    run = latest_run_id(conn)
    if run is None:
        raise LookupError("no matching run recorded yet; run `kx match` first")
    limits = conn.execute(RUN_LIMITS, {"run": run}).single()
    exchanges = run_exchanges(conn, run)
    before = frozenset(p for _, e in exchanges for p in e.patients)
    broken = tuple((ex_id, e) for ex_id, e in exchanges if donor_id in e.donors)

    previous = conn.autocommit
    conn.autocommit = False
    try:
        leaving = conn.execute(WITHDRAW, {"id": donor_id}).single()["leaving"]
        inputs = read_inputs(conn, limits["max_cycle"])
        replan = solve(inputs, limits["max_chain"])
    finally:
        conn.rollback()
        conn.autocommit = previous
    return WhatIf(donor_id, run, leaving, broken, before, replan, inputs)
