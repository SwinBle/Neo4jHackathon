import pytest

from kidneyloop.counterfactual import counterfactual, render
from kidneyloop.runs import run_match

from pool_builders import donor, patient

STATE = """
MATCH (n) WITH count(n) AS nodes
MATCH ()-[r]->() WITH nodes, count(r) AS rels
MATCH ()-[c:COMPATIBLE]->() WITH nodes, rels, count(c) AS arcs
MATCH ()-[h:HAS_ANTIBODY]->() RETURN nodes, rels, arcs, count(h) AS antibodies
"""


def bump_pool():
    """P1 is blocked from D2 by B8 and from D3 by A2; P2-P3 is the only cycle.

    Without B8, D2 can give to P1 and the cycle P1-P2 competes with P2-P3 for P2. P1's
    long wait wins the tie-break, so P3 loses their kidney. Without A2, D3 can give to
    P1 but only through the 3-way cycle P1-P2-P3, which a 2-cycle run does not allow.
    """
    patients = [patient("P1", antibodies=["B8", "A2"], wait_days=1000), patient("P2"), patient("P3", antibodies=["A3"], wait_days=10)]
    donors = [
        donor("D1", antigens=["A3"], paired_with="P1"),
        donor("D2", antigens=["B8"], paired_with="P2"),
        donor("D3", antigens=["A2"], paired_with="P3"),
    ]
    return patients, donors


def by_change(cf):
    return {t.change.split(" (")[0]: t for t in cf.trials}


def test_dropping_an_antibody_matches_the_patient_and_names_who_is_bumped(graph_of):
    conn = graph_of(*bump_pool())
    run_match(conn, max_cycle=2)
    before = conn.execute(STATE).single()
    cf = counterfactual(conn, "P1")
    trials = by_change(cf)
    b8 = trials["without the antibody to B8"]
    assert cf.matched(b8) and b8.plan.transplants == 2
    assert cf.lost(b8) == {"P3"}
    a2 = trials["without the antibody to A2"]
    assert a2.plan is not None and not cf.matched(a2)
    assert cf.matched(trials["3-way cycles allowed"]) is False
    assert cf.first_help is b8
    assert "P3 would lose a kidney" in render(cf)[-2]
    assert conn.execute(STATE).single() == before


def test_three_way_cycles_help_when_the_run_allowed_only_two(graph_of):
    patients, donors = bump_pool()
    patients[0] = patient("P1", antibodies=["B8"], wait_days=1000)
    conn = graph_of(patients, donors)
    run_match(conn, max_cycle=2)
    cf = counterfactual(conn, "P1", antigens=0)
    trial = by_change(cf)["3-way cycles allowed"]
    assert cf.matched(trial) and trial.plan.transplants == 3 and cf.lost(trial) == frozenset()


def test_a_longer_chain_reaches_the_patient(graph_of):
    patients = [patient("P1", antibodies=["A3"]), patient("P2", antibodies=["B8"])]
    donors = [donor("N1", antigens=["A3"]), donor("D1", antigens=["B8"], paired_with="P1"), donor("D2", paired_with="P2")]
    conn = graph_of(patients, donors)
    run_match(conn, max_chain=1)
    cf = counterfactual(conn, "P1")
    trial = by_change(cf)["chains of up to 2 instead of 1"]
    assert cf.matched(trial) and trial.plan.transplants == 2 and cf.lost(trial) == frozenset()


def test_no_single_change_helps(graph_of):
    patients = [patient("P1", antibodies=["B8", "A2"]), patient("P2")]
    donors = [donor("D1", paired_with="P1"), donor("D2", antigens=["B8", "A2"], paired_with="P2")]
    conn = graph_of(patients, donors)
    run_match(conn)
    before = conn.execute(STATE).single()
    cf = counterfactual(conn, "P1")
    assert [t.note for t in cf.trials[:2]] == ["opens no donor, not re-solved"] * 2
    assert cf.first_help is None
    assert "No single change tried gets P1 matched." in render(cf)
    assert conn.execute(STATE).single() == before


def test_uses_the_runs_policy_and_objective(graph_of):
    conn = graph_of(*bump_pool())
    run_match(conn, max_cycle=2, objective="expected")
    cf = counterfactual(conn, "P1", antigens=0)
    assert (cf.objective, cf.policy, cf.max_cycle, cf.max_chain) == ("expected", "default", 2, 4)


def test_refuses_a_matched_patient_or_a_pool_without_a_run(graph_of):
    conn = graph_of(*bump_pool())
    with pytest.raises(LookupError):
        counterfactual(conn, "P1")
    run_match(conn, max_cycle=2)
    with pytest.raises(ValueError):
        counterfactual(conn, "P2")


def test_requiring_the_patient_shows_who_pays_for_it(graph_of):
    """P1-P2 and P2-P3 are the only cycles and share P2; P3's longer wait wins the tie-break."""
    patients = [patient("P1", antibodies=["A2"], wait_days=10), patient("P2"), patient("P3", antibodies=["A3"], wait_days=1000)]
    donors = [
        donor("D1", antigens=["A3"], paired_with="P1"),
        donor("D2", antigens=["B8"], paired_with="P2"),
        donor("D3", antigens=["A2"], paired_with="P3"),
    ]
    conn = graph_of(patients, donors)
    run_match(conn, max_cycle=2)
    before = conn.execute(STATE).single()
    cf = counterfactual(conn, "P1", antigens=0)
    trial = by_change(cf)["P1 required in the plan"]
    assert cf.matched(trial) and trial.plan.transplants == 2 and cf.lost(trial) == {"P3"}
    assert "First change that gets P1 matched: P1 required in the plan; P3 would lose a kidney." in render(cf)
    assert conn.execute(STATE).single() == before
