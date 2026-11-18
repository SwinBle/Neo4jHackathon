from dataclasses import dataclass

import relate

from .exchanges import Exchange
from .runs import latest_run_id, read_inputs, run_exchanges
from .solver import MatchInputs, Plan, solve

WITHDRAW = "MATCH (d:Donor {id: $id}) DETACH DELETE d RETURN count(*) AS removed"
DONOR_EXISTS = "MATCH (d:Donor {id: $id}) RETURN count(d) AS n"


@dataclass(frozen=True)
class WhatIf:
    donor: str
    run: str
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
        return tuple(out)

    @property
    def after(self) -> frozenset[str]:
        return self.replan.matched_patients

    @property
    def lost(self) -> frozenset[str]:
        return self.before - self.after

    @property
    def gained(self) -> frozenset[str]:
        return self.after - self.before


def withdraw_donor(conn: relate.Connection, donor_id: str, max_cycle: int = 3, max_chain: int = 4) -> WhatIf:
    """Re-plan the latest run as if the donor had withdrawn, then roll the withdrawal back.

    The deletion and the re-plan happen inside one transaction that is always rolled
    back, so the store is unchanged afterwards.
    """
    if conn.execute(DONOR_EXISTS, {"id": donor_id}).single()["n"] == 0:
        raise KeyError(f"no donor {donor_id}")
    run = latest_run_id(conn)
    if run is None:
        raise LookupError("no matching run recorded yet; run `kx match` first")
    exchanges = run_exchanges(conn, run)
    before = frozenset(p for _, e in exchanges for p in e.patients)
    broken = tuple((ex_id, e) for ex_id, e in exchanges if donor_id in e.donors)

    previous = conn.autocommit
    conn.autocommit = False
    try:
        conn.execute(WITHDRAW, {"id": donor_id})
        inputs = read_inputs(conn, max_cycle)
        replan = solve(inputs, max_chain)
    finally:
        conn.rollback()
        conn.autocommit = previous
    return WhatIf(donor_id, run, broken, before, replan, inputs)
