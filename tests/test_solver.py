import random

import pytest
import relate

from kidneyloop.exchanges import Arc, Exchange
from kidneyloop.generator import generate_pool
from kidneyloop.solver import MatchInputs, patient_weight, plan_problems, plan_value, solve
from kidneyloop.store import derive, load_pool

from reference import all_exchanges, best_value


def inputs_from(arcs, pairs, weights=None, max_cycle=3):
    donor_of = {p: d for d, p in pairs}
    weight = weights or {p: 1.0 for p in {a[1] for a in arcs} | set(donor_of)}
    arc_objs = tuple(Arc(d, p, d not in set(donor_of.values())) for d, p in arcs)
    base = MatchInputs((), arc_objs, donor_of, weight)
    cycles = tuple(e for e in all_exchanges(base, max_cycle, 0) if e.kind == "cycle")
    return MatchInputs(cycles, arc_objs, donor_of, weight)


def test_three_way_cycle_beats_overlapping_two_way_cycle():
    pairs = [("D1", "P1"), ("D2", "P2"), ("D3", "P3")]
    arcs = [("D1", "P2"), ("D2", "P1"), ("D2", "P3"), ("D3", "P1")]
    plan = solve(inputs_from(arcs, pairs))
    assert plan.transplants == 3
    assert plan.exchanges[0].kind == "cycle"


def test_altruist_starts_a_chain_through_pairs_with_no_cycle():
    pairs = [("D1", "P1"), ("D2", "P2")]
    arcs = [("N1", "P1"), ("D1", "P2")]
    plan = solve(inputs_from(arcs, pairs))
    assert plan.exchanges == (Exchange("chain", ("N1", "D1"), ("P1", "P2")),)


def test_chain_stops_at_max_length():
    pairs = [(f"D{i}", f"P{i}") for i in range(1, 7)]
    arcs = [("N1", "P1")] + [(f"D{i}", f"P{i + 1}") for i in range(1, 6)]
    plan = solve(inputs_from(arcs, pairs), max_chain=4)
    assert plan.transplants == 4
    assert plan.exchanges[0].patients == ("P1", "P2", "P3", "P4")


def test_tie_break_prefers_highly_sensitized_patient():
    pairs = [("D1", "P1"), ("D2", "P2")]
    arcs = [("N1", "P1"), ("N1", "P2")]
    weights = {"P1": patient_weight(0.1, 100, 1000, 2), "P2": patient_weight(0.9, 100, 1000, 2)}
    plan = solve(inputs_from(arcs, pairs, weights))
    assert plan.matched_patients == {"P2"}


def test_tie_break_never_costs_a_transplant():
    n = 300
    worst_extra = n * (patient_weight(1.0, 2500, 2500, n) - 1.0)
    assert worst_extra < 1.0


def test_max_chain_zero_allows_no_chain():
    pairs = [("D1", "P1")]
    plan = solve(inputs_from([("N1", "P1")], pairs), max_chain=0)
    assert plan.transplants == 0


def test_empty_pool_gives_empty_plan():
    assert solve(MatchInputs((), (), {}, {})).transplants == 0


def random_inputs(seed: int) -> MatchInputs:
    rng = random.Random(seed)
    n_pairs, n_alt = rng.randint(2, 7), rng.randint(0, 2)
    pairs = [(f"D{i}", f"P{i}") for i in range(n_pairs)]
    donors = [d for d, _ in pairs] + [f"N{i}" for i in range(n_alt)]
    patients = [p for _, p in pairs]
    density = rng.uniform(0.15, 0.6)
    arcs = [(d, p) for d in donors for p in patients if not d[1:] == p[1:] or d[0] == "N" if rng.random() < density]
    weights = {p: patient_weight(rng.random(), rng.randint(1, 2500), 2500, n_pairs) for p in patients}
    return inputs_from(arcs, pairs, weights)


@pytest.mark.parametrize("seed", range(150))
def test_solver_matches_brute_force_optimum_and_rules(seed):
    inputs = random_inputs(seed)
    plan = solve(inputs, max_chain=3)
    assert plan_problems(plan, inputs, max_cycle=3, max_chain=3) == []
    assert plan_value(plan, inputs) == pytest.approx(best_value(inputs, 3, 3), abs=1e-9)


def test_problems_catch_a_broken_plan():
    pairs = [("D1", "P1"), ("D2", "P2")]
    inputs = inputs_from([("D1", "P2"), ("D2", "P1")], pairs)
    bad = solve(inputs).__class__(
        (Exchange("cycle", ("D1", "D2"), ("P1", "P2")), Exchange("chain", ("D1",), ("P1",)))
    )
    problems = plan_problems(bad, inputs)
    assert any("cannot give" in p for p in problems)
    assert any("more than one kidney" in p for p in problems)
    assert any("altruistic" in p for p in problems)


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_cypher_cycles_equal_reference_cycles_on_generated_pools(seed):
    from kidneyloop.exchanges import find_arcs, find_cycles

    pool = generate_pool(seed=seed, pairs=25, altruists=2)
    conn = relate.connect(":memory:")
    load_pool(conn, pool)
    derive(conn)
    arcs = tuple(find_arcs(conn))
    donor_of = {d.paired_with: d.id for d in pool.donors if d.paired_with}
    base = MatchInputs((), arcs, donor_of, {})
    expected = {e for e in all_exchanges(base, 3, 0) if e.kind == "cycle"}
    assert set(find_cycles(conn, 3)) == expected
    conn.close()
