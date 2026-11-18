from dataclasses import dataclass

import relate

from .explain import explain
from .runs import Progress, _quiet, read_inputs, run_exchanges, run_objective, run_policy, run_record, solve_with_progress
from .solver import Objective, Plan
from .store import derive

DROP_ANTIBODY = "MATCH (:Patient {id: $id})-[h:HAS_ANTIBODY]->(:Antigen {name: $antigen}) DELETE h"
DROP_COMPATIBLE = "MATCH ()-[c:COMPATIBLE]->() DELETE c"
INCOMING = "MATCH (d:Donor)-[:COMPATIBLE]->(:Patient {id: $id}) RETURN count(d) AS n"


@dataclass(frozen=True)
class Trial:
    """One single change to the run, and the plan a re-solve gave with it; plan is None when it was not re-solved."""

    change: str
    note: str
    plan: Plan | None


@dataclass(frozen=True)
class Counterfactual:
    patient: str
    run: str
    max_cycle: int
    max_chain: int
    policy: str
    objective: Objective
    before: Plan
    trials: tuple[Trial, ...]

    def matched(self, trial: Trial) -> bool:
        return trial.plan is not None and self.patient in trial.plan.matched_patients

    def lost(self, trial: Trial) -> frozenset[str]:
        """Patients the run serves who would go without a kidney after the change."""
        return self.before.matched_patients - trial.plan.matched_patients if trial.plan else frozenset()

    def moved(self, trial: Trial) -> int:
        """Patients served both before and after, but by a different donor."""
        before = {p: d for e in self.before.exchanges for d, p in zip(e.donors, e.patients)}
        after = {p: d for e in trial.plan.exchanges for d, p in zip(e.donors, e.patients)} if trial.plan else {}
        return sum(1 for p in before.keys() & after.keys() if before[p] != after[p])

    @property
    def first_help(self) -> Trial | None:
        return next((t for t in self.trials if self.matched(t)), None)


def counterfactual(
    conn: relate.Connection, patient_id: str, progress: Progress = _quiet, antigens: int = 3
) -> Counterfactual:
    """Try single changes that might get an unmatched patient into the latest run's plan.

    Each change is checked by re-solving the whole pool with the run's limits, policy
    and objective. A changed antibody is removed inside a transaction that is always
    rolled back, so the store is unchanged afterwards. Up to `antigens` antibodies are
    tried, those blocking the most donors first, then a chain one transplant longer,
    then 3-way cycles if the run allowed only 2-way ones.
    """
    why = explain(conn, patient_id)
    if why.run is None:
        raise LookupError("no matching run recorded yet; run `kx match` first")
    if why.matched_in is not None:
        raise ValueError(f"{patient_id} is already matched in {why.run} by {why.matched_in[0]}")
    run = run_record(conn, why.run)
    max_cycle, max_chain = run["max_cycle"], run["max_chain"]
    policy, objective = run_policy(run), run_objective(run)
    before = Plan(tuple(e for _, e in run_exchanges(conn, why.run)))
    trials: list[Trial] = []

    for antigen, blocked in why.blocking_antigens[:antigens]:
        change = f"without the antibody to {antigen} (it blocks {blocked} blood-compatible donors)"
        progress(f"Trying {change} inside a transaction...")
        previous = conn.autocommit
        conn.autocommit = False
        try:
            conn.execute(DROP_ANTIBODY, {"id": patient_id, "antigen": antigen})
            conn.execute(DROP_COMPATIBLE)
            derive(conn)
            opened = conn.execute(INCOMING, {"id": patient_id}).single()["n"] - why.incoming
            if opened == 0:
                # Every donor this antibody blocks carries another antigen the patient reacts to,
                # so the patient's possible exchanges are the same as in the run.
                trials.append(Trial(change, "opens no donor, not re-solved", None))
                continue
            inputs = read_inputs(conn, max_cycle, progress, policy)
            plan = solve_with_progress(inputs, max_chain, progress, objective)
            trials.append(Trial(change, f"opens {opened} donor{'s' if opened > 1 else ''}", plan))
        finally:
            conn.rollback()
            conn.autocommit = previous

    progress("Reading the pool with the run's limits for the limit changes...")
    inputs = read_inputs(conn, max_cycle, progress, policy)
    longer = max_chain + 1
    trials.append(Trial(f"chains of up to {longer} instead of {max_chain}", "", solve_with_progress(inputs, longer, progress, objective)))
    if max_cycle == 2:
        wider = read_inputs(conn, 3, progress, policy)
        trials.append(Trial("3-way cycles allowed", "", solve_with_progress(wider, max_chain, progress, objective)))
    return Counterfactual(patient_id, why.run, max_cycle, max_chain, policy.name, objective, before, tuple(trials))


def render(cf: Counterfactual) -> list[str]:
    lines = [
        f"{cf.patient} is not matched in {cf.run} ({cf.objective} objective, policy {cf.policy}, "
        f"cycles of up to {cf.max_cycle}, chains of up to {cf.max_chain}: {cf.before.transplants} transplants).",
        "Single changes, each checked by re-solving the whole pool:",
    ]
    for t in cf.trials:
        if t.plan is None:
            lines.append(f"  {t.change}: {t.note}")
            continue
        lost = cf.lost(t)
        delta = t.plan.transplants - cf.before.transplants
        lines.append(
            f"  {t.change}: {t.note + '; ' if t.note else ''}{cf.patient} {'matched' if cf.matched(t) else 'still unmatched'}; "
            f"{cf.before.transplants} -> {t.plan.transplants} transplants ({delta:+d}); "
            f"lose a kidney: {', '.join(sorted(lost)) or 'none'}; {cf.moved(t)} moved to a different donor"
        )
    best = cf.first_help
    if best is None:
        lines.append(f"No single change tried gets {cf.patient} matched.")
    else:
        lost = cf.lost(best)
        cost = f"{', '.join(sorted(lost))} would lose a kidney" if lost else "nobody the run serves loses a kidney"
        lines.append(f"First change that gets {cf.patient} matched: {best.change}; {cost}.")
    lines.append("(antibody changes rolled back; the store is unchanged)")
    return lines
