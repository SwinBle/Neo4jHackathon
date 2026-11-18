from dataclasses import dataclass

import relate

from .exchanges import Exchange
from .runs import Progress, _quiet, latest_run_id, read_inputs, run_exchanges, solve_with_progress
from .solver import MatchInputs, Plan

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
PAIRED_PATIENT = "MATCH (d:Donor {id: $id}) OPTIONAL MATCH (d)-[:PAIRED_WITH]->(p:Patient) RETURN p.id AS patient"
RUN_LIMITS = "MATCH (r:MatchRun {id: $run}) RETURN r.max_cycle AS max_cycle, r.max_chain AS max_chain"


@dataclass(frozen=True)
class WhatIf:
    donor: str
    run: str
    leaving: str | None
    planned: tuple[tuple[str, Exchange], ...]
    broken: tuple[tuple[str, Exchange], ...]
    before: frozenset[str]
    replan: Plan
    replan_inputs: MatchInputs
    fresh: Plan

    @staticmethod
    def _donor_by_patient(exchanges) -> dict[str, str]:
        return {p: d for e in exchanges for d, p in zip(e.donors, e.patients)}

    @property
    def fresh_moved(self) -> frozenset[str]:
        """Patients a from-scratch re-plan would serve with a different donor than planned."""
        before = self._donor_by_patient(e for _, e in self.planned)
        after = self._donor_by_patient(self.fresh.exchanges)
        return frozenset(p for p in before.keys() & after.keys() if before[p] != after[p])

    @property
    def stranded(self) -> tuple[str, ...]:
        """Patients of the current plan who were waiting on this donor or a donor after it.

        A cycle needs every donor, so all its patients are stranded. A chain still
        serves the patients before the withdrawn donor's position.
        """
        out: list[str] = []
        for _, e in self.broken:
            if e.kind == "cycle":
                out.extend(e.patients)
            elif self.donor in e.donors:
                out.extend(e.patients[e.donors.index(self.donor):])
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

    @property
    def reassigned(self) -> frozenset[str]:
        """Patients served both before and after, but by a different donor.

        Each of them needs a new crossmatch, so the re-plan is not free for them
        even though they keep a kidney.
        """
        before = self._donor_by_patient(e for _, e in self.planned)
        after = self._donor_by_patient(self.replan.exchanges)
        return frozenset(p for p in before.keys() & after.keys() if before[p] != after[p])


def withdraw_donor(conn: relate.Connection, donor_id: str, progress: Progress = _quiet) -> WhatIf:
    """Repair the latest run as if the donor had withdrawn, then roll the withdrawal back.

    Exchanges the withdrawal does not touch are kept as they are; only the freed and
    unmatched patients are re-planned, with the latest run's cycle and chain limits. The deletion and the
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
    leaving = conn.execute(PAIRED_PATIENT, {"id": donor_id}).single()["patient"]
    before = frozenset(p for _, e in exchanges for p in e.patients)
    broken = tuple(
        (ex_id, e) for ex_id, e in exchanges if donor_id in e.donors or (leaving is not None and leaving in e.patients)
    )
    kept = [e for ex_id, e in exchanges if (ex_id, e) not in broken]

    previous = conn.autocommit
    conn.autocommit = False
    try:
        conn.execute(WITHDRAW, {"id": donor_id})
        progress(f"Withdrew {donor_id} inside a transaction; re-enumerating cycles...")
        inputs = read_inputs(conn, limits["max_cycle"], progress)
        repaired = solve_with_progress(_without(inputs, kept), limits["max_chain"], progress)
        progress("For comparison, re-planning the whole pool from scratch...")
        fresh = solve_with_progress(inputs, limits["max_chain"], progress)
    finally:
        conn.rollback()
        conn.autocommit = previous
    replan = Plan(tuple(kept) + repaired.exchanges)
    return WhatIf(donor_id, run, leaving, tuple(exchanges), broken, before, replan, inputs, fresh)


def _without(inputs: MatchInputs, kept: list[Exchange]) -> MatchInputs:
    """The pool minus everyone in an exchange that the withdrawal leaves intact.

    Real programs do not reshuffle accepted matches when one donor drops out: each
    new donor-patient pairing needs a fresh crossmatch. So only the people freed by
    broken exchanges, plus those still unmatched, are re-planned.
    """
    patients = {p for e in kept for p in e.patients}
    donors = {d for e in kept for d in e.donors}
    return MatchInputs(
        cycles=tuple(c for c in inputs.cycles if patients.isdisjoint(c.patients) and donors.isdisjoint(c.donors)),
        arcs=tuple(a for a in inputs.arcs if a.patient not in patients and a.donor not in donors),
        donor_of={p: d for p, d in inputs.donor_of.items() if p not in patients and d not in donors},
        weight=inputs.weight,
        success=inputs.success,
    )
