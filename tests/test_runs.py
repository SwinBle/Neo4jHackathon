import pytest

from kidneyloop.exchanges import Exchange
from kidneyloop.explain import explain, render
from kidneyloop.runs import latest_run_id, read_inputs, run_exchanges, run_match
from kidneyloop.solver import plan_problems
from kidneyloop.whatif import withdraw_donor

from pool_builders import donor, patient

COUNTS = "MATCH (n) WITH count(n) AS nodes MATCH ()-[r]->() RETURN nodes, count(r) AS rels"


def swap_pool():
    patients = [patient("P1", antibodies=["B8"]), patient("P2", blood="A"), patient("P3", blood="AB"), patient("P4", blood="B")]
    donors = [
        donor("D1", blood="A", paired_with="P1"),
        donor("D2", blood="O", paired_with="P2"),
        donor("D3", blood="AB", paired_with="P3"),
        donor("D4", blood="AB", paired_with="P4"),
        donor("N1", blood="B", antigens=["A2"]),
    ]
    return patients, donors


def test_recorded_run_reads_back_as_the_solved_plan(graph_of):
    conn = graph_of(*swap_pool())
    run = run_match(conn)
    assert latest_run_id(conn) == "R1"
    read_back = {e for _, e in run_exchanges(conn, "R1")}
    assert read_back == set(run.plan.exchanges)
    assert plan_problems(run.plan, read_inputs(conn)) == []


def test_runs_are_numbered_and_latest_wins(graph_of):
    conn = graph_of(*swap_pool())
    run_match(conn)
    run_match(conn, max_chain=0)
    assert latest_run_id(conn) == "R2"


def test_why_reports_the_exchange_for_a_matched_patient(graph_of):
    conn = graph_of(*swap_pool())
    run = run_match(conn)
    matched = sorted(run.plan.matched_patients)[0]
    why = explain(conn, matched)
    assert why.matched_in is not None and matched in why.matched_in[1].patients
    assert any("Matched in R1" in line for line in render(why))


def test_why_names_antibodies_when_no_donor_can_give(graph_of):
    patients = [patient("P1", blood="O", antibodies=["B8", "A2"]), patient("P2")]
    donors = [donor("D1", paired_with="P1"), donor("D2", antigens=["B8"], paired_with="P2"), donor("N1", antigens=["A2", "B8"])]
    conn = graph_of(patients, donors)
    run_match(conn)
    why = explain(conn, "P1")
    assert why.incoming == 0
    assert dict(why.blocking_antigens) == {"B8": 2, "A2": 1}
    assert any("antigen the patient has antibodies to" in line for line in render(why))


def test_why_unknown_patient_raises(graph_of):
    conn = graph_of(*swap_pool())
    with pytest.raises(KeyError):
        explain(conn, "P999")


def test_whatif_rolls_back_and_reports_the_broken_exchange(graph_of):
    patients = [patient("P1"), patient("P2")]
    donors = [donor("D1", paired_with="P1"), donor("D2", paired_with="P2")]
    conn = graph_of(patients, donors)
    run_match(conn)
    before = conn.execute(COUNTS).single()
    result = withdraw_donor(conn, "D2")
    assert [e for _, e in result.broken] == [Exchange("cycle", ("D2", "D1"), ("P1", "P2"))]
    assert result.leaving == "P2"
    assert result.lost == {"P1"}
    assert conn.execute(COUNTS).single() == before
    assert conn.execute("MATCH (d:Donor {id: 'D2'}) RETURN count(d) AS n").single()["n"] == 1


def test_whatif_chain_keeps_patients_before_the_withdrawn_donor(graph_of):
    patients = [patient("P1"), patient("P2"), patient("P3")]
    donors = [donor("N1"), donor("D1", paired_with="P1"), donor("D2", paired_with="P2"), donor("D3", paired_with="P3")]
    conn = graph_of(patients, donors)
    for statement in [
        "MATCH ()-[c:COMPATIBLE]->() DELETE c",
        "MATCH (n:Donor {id: 'N1'}), (p:Patient {id: 'P1'}) CREATE (n)-[:COMPATIBLE]->(p)",
        "MATCH (d:Donor {id: 'D1'}), (p:Patient {id: 'P2'}) CREATE (d)-[:COMPATIBLE]->(p)",
        "MATCH (d:Donor {id: 'D2'}), (p:Patient {id: 'P3'}) CREATE (d)-[:COMPATIBLE]->(p)",
    ]:
        conn.execute(statement)
    run_match(conn)
    result = withdraw_donor(conn, "D2")
    assert [e for _, e in result.broken] == [Exchange("chain", ("N1", "D1", "D2"), ("P1", "P2", "P3"))]
    assert result.leaving == "P2"
    assert result.stranded == ("P3",)
    assert result.after == {"P1"}
    assert result.lost == {"P3"}


def test_whatif_replan_never_serves_the_withdrawn_pair_and_keeps_run_limits(graph_of):
    patients = [patient("P1"), patient("P2"), patient("P3")]
    donors = [donor("N1"), donor("D1", paired_with="P1"), donor("D2", paired_with="P2"), donor("D3", paired_with="P3")]
    conn = graph_of(patients, donors)
    run_match(conn, max_cycle=2, max_chain=0)
    result = withdraw_donor(conn, "D1")
    assert "P1" not in result.after
    assert all(e.kind == "cycle" and e.size == 2 for e in result.replan.exchanges)


def test_whatif_needs_a_run_and_a_real_donor(graph_of):
    conn = graph_of(*swap_pool())
    with pytest.raises(LookupError):
        withdraw_donor(conn, "D1")
    run_match(conn)
    with pytest.raises(KeyError):
        withdraw_donor(conn, "D999")
