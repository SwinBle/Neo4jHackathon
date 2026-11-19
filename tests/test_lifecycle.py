import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest
from typer.testing import CliRunner

from kidneyloop.cli import app
from kidneyloop.lifecycle import TRANSITIONS, TransitionError, set_status, statuses
from kidneyloop.model import Donor, Patient
from kidneyloop.runs import check_inputs, input_sha256, run_match

REPO = Path(__file__).resolve().parents[1]
AT = datetime(2026, 11, 19, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def two_swaps(graph_of):
    """Two separate 2-way swaps, P1-P2 and P3-P4, and nothing else."""
    patients = [
        Patient("P1", "A", frozenset({"A3", "A11"}), 10),
        Patient("P2", "A", frozenset({"A3", "A11"}), 10),
        Patient("P3", "A", frozenset({"A1", "A2"}), 10),
        Patient("P4", "A", frozenset({"A1", "A2"}), 10),
    ]
    donors = [
        Donor("D1", "A", frozenset({"A1"}), "P1"),
        Donor("D2", "A", frozenset({"A2"}), "P2"),
        Donor("D3", "A", frozenset({"A3"}), "P3"),
        Donor("D4", "A", frozenset({"A11"}), "P4"),
    ]
    conn = graph_of(patients, donors)
    run_match(conn)
    return conn


def exchange_of(conn, patient: str) -> str:
    return conn.execute(
        "MATCH (:MatchRun {id: 'R1'})-[:SELECTED]->(e:Exchange)-[:INCLUDES]->(:Patient {id: $p}) RETURN e.id AS id", {"p": patient}
    ).single()["id"]


def planned_patients(conn, run: str) -> set[str]:
    rows = conn.execute("MATCH (:MatchRun {id: $r})-[:SELECTED]->(:Exchange)-[:INCLUDES {role: 'patient'}]->(p) RETURN p.id AS id", {"r": run})
    return {r["id"] for r in rows}


def test_a_full_path_records_one_event_per_step(two_swaps):
    ex = exchange_of(two_swaps, "P1")
    for status in ("accepted", "crossmatch_passed", "scheduled", "transplanted"):
        assert set_status(two_swaps, ex, status, f"note {status}", AT).recorded
    events = list(two_swaps.execute(
        "MATCH (:Exchange {id: $id})-[:HAD_STATUS]->(s) RETURN s.seq AS seq, s.previous AS previous, s.status AS status, s.at AS at ORDER BY s.seq",
        {"id": ex},
    ))
    assert [(e["seq"], e["previous"], e["status"]) for e in events] == [
        (1, "proposed", "accepted"),
        (2, "accepted", "crossmatch_passed"),
        (3, "crossmatch_passed", "scheduled"),
        (4, "scheduled", "transplanted"),
    ]
    assert events[0]["at"] == "2026-11-19T09:00:00+00:00"


def test_repeating_the_current_status_records_nothing(two_swaps):
    ex = exchange_of(two_swaps, "P1")
    set_status(two_swaps, ex, "accepted", at=AT)
    again = set_status(two_swaps, ex, "accepted", at=AT)
    assert not again.recorded
    assert again.summary() == f"{ex} is already accepted; nothing recorded."
    assert two_swaps.execute("MATCH (:Exchange {id: $id})-[:HAD_STATUS]->(s) RETURN count(s) AS n", {"id": ex}).single()["n"] == 1


@pytest.mark.parametrize(
    ("path", "illegal"),
    [((), "transplanted"), ((), "scheduled"), (("accepted",), "declined"), (("declined",), "accepted"), (("accepted", "cancelled"), "accepted")],
)
def test_illegal_transitions_are_refused(two_swaps, path, illegal):
    ex = exchange_of(two_swaps, "P1")
    for status in path:
        set_status(two_swaps, ex, status, at=AT)
    with pytest.raises(TransitionError, match=f"not {illegal}"):
        set_status(two_swaps, ex, illegal, at=AT)


def test_every_failure_state_is_final_and_reachable():
    finals = [s for s, nxt in TRANSITIONS.items() if not nxt]
    assert set(finals) == {"transplanted", "declined", "crossmatch_failed", "cancelled"}
    reachable = {"proposed"} | {t for nxt in TRANSITIONS.values() for t in nxt}
    assert reachable == set(TRANSITIONS)


def test_unknown_status_and_exchange(two_swaps):
    with pytest.raises(TransitionError, match="unknown status"):
        set_status(two_swaps, exchange_of(two_swaps, "P1"), "done")
    with pytest.raises(KeyError):
        set_status(two_swaps, "R9-X9", "accepted")


def test_held_exchanges_stay_out_of_the_next_round_and_failures_come_back(two_swaps):
    ex = exchange_of(two_swaps, "P1")
    change = set_status(two_swaps, ex, "accepted", at=AT)
    assert "stay out of the next round" in change.summary()
    run_match(two_swaps)
    assert planned_patients(two_swaps, "R2") == {"P3", "P4"}
    failed = set_status(two_swaps, ex, "crossmatch_failed", at=AT)
    assert "Back in the pool" in failed.summary() and "P1" in failed.summary()
    run_match(two_swaps)
    assert planned_patients(two_swaps, "R3") == {"P1", "P2", "P3", "P4"}


def test_transplanted_patients_and_their_donors_leave_the_pool(two_swaps):
    ex = exchange_of(two_swaps, "P3")
    for status in ("accepted", "crossmatch_passed", "scheduled", "transplanted"):
        set_status(two_swaps, ex, status, at=AT)
    run_match(two_swaps)
    assert planned_patients(two_swaps, "R2") == {"P1", "P2"}


def test_a_runs_own_statuses_do_not_make_its_inputs_stale(two_swaps):
    set_status(two_swaps, exchange_of(two_swaps, "P1"), "accepted", at=AT)
    assert check_inputs(two_swaps, "R1").warning() is None
    assert input_sha256(two_swaps, 3, 4, "p", "count") != input_sha256(two_swaps, 3, 4, "p", "count", before=1)


def test_status_list_covers_the_latest_run_and_earlier_exchanges_with_a_status(two_swaps):
    ex = exchange_of(two_swaps, "P1")
    set_status(two_swaps, ex, "accepted", at=AT)
    run_match(two_swaps)
    rows = statuses(two_swaps, "R2")
    assert [(r["run"], r["status"]) for r in rows if r["run"] == "R1"] == [("R1", "accepted")]
    assert {r["run"] for r in rows} == {"R1", "R2"}


def test_cli_status_on_a_copy_of_the_committed_store(tmp_path):
    store = tmp_path / "store"
    shutil.copytree(REPO / "data" / "kidneyloop", store)
    run = CliRunner()
    listed = run.invoke(app, ["status", "--store", str(store)])
    assert listed.exit_code == 0, listed.output
    assert listed.output.strip().endswith("66 proposed")
    moved = run.invoke(app, ["status", "R1-X1", "--set", "accepted", "--note", "both centres agreed", "--store", str(store)])
    assert moved.exit_code == 0, moved.output
    assert moved.output.startswith("R1-X1: proposed -> accepted.")
    again = run.invoke(app, ["status", "r1-x1", "--set", "accepted", "--store", str(store)])
    assert "already accepted" in again.output
    illegal = run.invoke(app, ["status", "R1-X1", "--set", "transplanted", "--store", str(store)])
    assert illegal.exit_code == 2
    assert "can move to crossmatch_passed, crossmatch_failed, cancelled" in illegal.output
    audit = run.invoke(app, ["audit", "--store", str(store)])
    assert "inputs unchanged" in audit.output


def test_the_summary_names_the_same_people_the_next_round_leaves_out(two_swaps):
    from kidneyloop.runs import held

    change = set_status(two_swaps, exchange_of(two_swaps, "P1"), "accepted", at=AT)
    patients, donors = held(two_swaps)
    assert set(change.patients) == patients
    assert set(change.donors) == donors


def test_only_the_latest_runs_proposals_can_be_accepted(two_swaps):
    older = exchange_of(two_swaps, "P1")
    run_match(two_swaps)  # R2 plans P1-P2 again, since nothing is held yet
    with pytest.raises(TransitionError, match="later run replaced"):
        set_status(two_swaps, older, "accepted", at=AT)
    assert two_swaps.execute("MATCH (:Exchange {id: $id})-[:HAD_STATUS]->(s) RETURN count(s) AS n", {"id": older}).single()["n"] == 0
    assert set_status(two_swaps, older, "declined", at=AT).recorded


def test_an_accepted_exchange_of_an_earlier_run_keeps_moving(two_swaps):
    ex = exchange_of(two_swaps, "P1")
    set_status(two_swaps, ex, "accepted", at=AT)
    run_match(two_swaps)
    assert "P1" not in planned_patients(two_swaps, "R2")
    assert set_status(two_swaps, ex, "crossmatch_passed", at=AT).recorded
