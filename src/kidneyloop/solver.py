from collections import defaultdict
from dataclasses import dataclass
from typing import Mapping

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import coo_array

from .exchanges import Arc, Exchange


@dataclass(frozen=True)
class MatchInputs:
    """Everything the solver needs, read from the graph.

    donor_of maps each paired patient to the donor who came with them.
    weight is the solver's value for one transplant to that patient.
    """

    cycles: tuple[Exchange, ...]
    arcs: tuple[Arc, ...]
    donor_of: Mapping[str, str]
    weight: Mapping[str, float]


@dataclass(frozen=True)
class Plan:
    exchanges: tuple[Exchange, ...]

    @property
    def transplants(self) -> int:
        return sum(e.size for e in self.exchanges)

    @property
    def matched_patients(self) -> frozenset[str]:
        return frozenset(p for e in self.exchanges for p in e.patients)


def plan_problems(plan: Plan, inputs: MatchInputs, max_cycle: int = 3, max_chain: int = 4) -> list[str]:
    """Every way the plan breaks the matching rules; an empty list means it is valid."""
    problems: list[str] = []
    arcs = {(a.donor, a.patient) for a in inputs.arcs}
    altruists = {a.donor for a in inputs.arcs if a.altruistic}
    seen_patients: set[str] = set()
    seen_donors: set[str] = set()
    for e in plan.exchanges:
        if len(e.donors) != len(e.patients):
            problems.append(f"{e}: donors and patients differ in count")
            continue
        for d, p in zip(e.donors, e.patients):
            if (d, p) not in arcs:
                problems.append(f"{e}: {d} cannot give to {p}")
        if e.kind == "cycle":
            if not 2 <= e.size <= max_cycle:
                problems.append(f"{e}: cycle size {e.size} outside 2..{max_cycle}")
            expected = tuple(inputs.donor_of.get(p) for p in e.patients[-1:] + e.patients[:-1])
            if e.donors != expected:
                problems.append(f"{e}: donors are not the previous pairs' donors")
        else:
            if e.donors[0] not in altruists:
                problems.append(f"{e}: chain does not start at an altruistic donor")
            if not 1 <= e.size <= max_chain:
                problems.append(f"{e}: chain size {e.size} outside 1..{max_chain}")
            expected = tuple(inputs.donor_of.get(p) for p in e.patients[:-1])
            if e.donors[1:] != expected:
                problems.append(f"{e}: chain donors are not the previous pairs' donors")
        for p in e.patients:
            if p in seen_patients:
                problems.append(f"{p} receives more than one kidney")
            seen_patients.add(p)
        for d in e.donors:
            if d in seen_donors:
                problems.append(f"{d} gives more than one kidney")
            seen_donors.add(d)
    return problems


def plan_value(plan: Plan, inputs: MatchInputs) -> float:
    return sum(inputs.weight[p] for p in plan.matched_patients)


def patient_weight(pool_pra: float, wait_days: int, max_wait: int, patients: int) -> float:
    """One per transplant, plus a tie-break below 1/patients.

    The tie-break favours hard-to-match (high pool PRA) and long-waiting patients.
    Summed over every patient it stays below one transplant, so the solver never
    trades a transplant for tie-break value.
    """
    tie = 0.7 * pool_pra + 0.3 * (wait_days / max_wait if max_wait else 0.0)
    return 1.0 + tie / (patients + 1)


def solve(inputs: MatchInputs, max_chain: int = 4) -> Plan:
    """Pick disjoint cycles and chains that maximise total patient weight.

    Cycles come in pre-enumerated. Chains are built from arcs with one binary
    variable per arc and chain position: position 1 arcs leave an altruistic
    donor, and a paired donor gives at position k+1 only if its own patient
    received at position k.
    """
    # Several plans often tie on value, and HiGHS returns the first it finds, which
    # depends on variable order. Sorting makes the plan independent of the order
    # Relate happens to return rows in, so the same pool always gives the same plan.
    cycles = sorted(inputs.cycles, key=lambda c: (c.patients, c.donors))
    arcs = sorted(inputs.arcs, key=lambda a: (a.donor, a.patient))
    paired_donors = set(inputs.donor_of.values())
    first = [a for a in arcs if a.altruistic] if max_chain >= 1 else []
    later = [a for a in arcs if not a.altruistic and a.donor in paired_donors]
    chain_vars = [(a, 1) for a in first] + [(a, k) for k in range(2, max_chain + 1) for a in later]

    n_vars = len(cycles) + len(chain_vars)
    value = np.zeros(n_vars)
    rows: list[int] = []
    cols: list[int] = []
    vals: list[float] = []
    upper: list[float] = []

    def add_row(terms: list[tuple[int, float]], ub: float) -> None:
        r = len(upper)
        for c, v in terms:
            rows.append(r)
            cols.append(c)
            vals.append(v)
        upper.append(ub)

    receives: dict[str, list[int]] = defaultdict(list)
    for i, cycle in enumerate(cycles):
        value[i] = sum(inputs.weight[p] for p in cycle.patients)
        for p in cycle.patients:
            receives[p].append(i)

    gives_at: dict[tuple[str, int], list[int]] = defaultdict(list)
    receives_at: dict[tuple[str, int], list[int]] = defaultdict(list)
    for j, (arc, k) in enumerate(chain_vars, start=len(cycles)):
        value[j] = inputs.weight[arc.patient]
        receives[arc.patient].append(j)
        gives_at[(arc.donor, k)].append(j)
        receives_at[(arc.patient, k)].append(j)

    for patient, vars_ in receives.items():
        add_row([(v, 1.0) for v in vars_], 1.0)
    for donor in sorted({a.donor for a in first}):
        add_row([(v, 1.0) for v in gives_at[(donor, 1)]], 1.0)
    for patient, donor in inputs.donor_of.items():
        for k in range(1, max_chain):
            out = gives_at.get((donor, k + 1), [])
            if out:
                add_row([(v, 1.0) for v in out] + [(v, -1.0) for v in receives_at.get((patient, k), [])], 0.0)

    if n_vars == 0:
        return Plan(())
    constraints = []
    if upper:
        matrix = coo_array((vals, (rows, cols)), shape=(len(upper), n_vars)).tocsr()
        constraints.append(LinearConstraint(matrix, -np.inf, np.array(upper)))
    result = milp(
        -value,
        constraints=constraints,
        integrality=np.ones(n_vars),
        bounds=Bounds(0, 1),
        options={"mip_rel_gap": 0},
    )
    if not result.success:
        raise RuntimeError(f"solver failed: {result.message}")
    chosen = result.x > 0.5

    exchanges = [cycles[i] for i in range(len(cycles)) if chosen[i]]
    taken = {(arc.donor, k): arc for j, (arc, k) in enumerate(chain_vars, start=len(cycles)) if chosen[j]}
    for (donor, k), arc in sorted(taken.items()):
        if k != 1:
            continue
        donors, patients = [arc.donor], [arc.patient]
        while (nxt := taken.get((inputs.donor_of.get(patients[-1], ""), len(patients) + 1))) is not None:
            donors.append(nxt.donor)
            patients.append(nxt.patient)
        exchanges.append(Exchange("chain", tuple(donors), tuple(patients)))
    return Plan(tuple(exchanges))
