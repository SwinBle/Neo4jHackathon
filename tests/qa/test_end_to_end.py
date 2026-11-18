import hashlib
import json
import subprocess
from pathlib import Path

import pytest
import relate

from kidneyloop.explain import explain
from kidneyloop.runs import latest_run_id, read_inputs, run_exchanges
from kidneyloop.solver import Plan, plan_problems, plan_value, solve
from kidneyloop.store import relate_cli
from kidneyloop.whatif import withdraw_donor

from kx_runner import kx

pytestmark = pytest.mark.qa
REPO = Path(__file__).resolve().parents[2]


def fingerprint(store: Path) -> dict[str, str]:
    return {
        str(p.relative_to(store)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(store.rglob("*"))
        if p.is_file()
    }


def latest_plan(conn) -> Plan:
    return Plan(tuple(e for _, e in run_exchanges(conn, latest_run_id(conn))))


def assert_run_is_sound(store: Path) -> None:
    """The recorded plan obeys every rule and leaves no exchange the solver could have added."""
    with relate.connect(str(store), read_only=True) as conn:
        inputs = read_inputs(conn)
        plan = latest_plan(conn)
        recorded = conn.execute("MATCH (r:MatchRun) RETURN r.transplants AS t ORDER BY r.seq DESC LIMIT 1").single()["t"]
        assert plan_problems(plan, inputs) == []
        assert plan.transplants == recorded
        served_patients = plan.matched_patients
        used_donors = {d for e in plan.exchanges for d in e.donors}
        free_cycles = [c for c in inputs.cycles if served_patients.isdisjoint(c.patients)]
        assert free_cycles == [], f"cycle left on the table: {free_cycles[0]}"
        free_starts = [a for a in inputs.arcs if a.altruistic and a.donor not in used_donors and a.patient not in served_patients]
        assert free_starts == [], f"unused altruist could still give: {free_starts[0]}"
        assert plan_value(plan, inputs) == pytest.approx(plan_value(solve(inputs), inputs), abs=1e-9)
        for patient in inputs.weight:
            why = explain(conn, patient)
            assert (why.matched_in is not None) == (patient in served_patients)


def test_build_is_reproducible(tmp_path):
    for name in ("a", "b"):
        result = kx("build", "--store", str(tmp_path / name), "--pairs", "30", "--altruists", "2", cwd=tmp_path)
        assert result.returncode == 0, result.stderr
    assert (tmp_path / "a" / "graph.json").read_bytes() == (tmp_path / "b" / "graph.json").read_bytes()


def test_relate_cli_reads_the_same_graph_python_wrote(built_store):
    query = "MATCH (p:Patient) WITH count(p) AS patients MATCH ()-[c:COMPATIBLE]->() RETURN patients, count(c) AS arcs"
    cli = subprocess.run(
        [relate_cli(), "--database", str(built_store), "--read-only", "--mode", "envelope", query],
        capture_output=True, text=True, check=True,
    )
    from_cli = json.loads(cli.stdout.splitlines()[0])["rows"][0]
    with relate.connect(str(built_store), read_only=True) as conn:
        from_python = dict(conn.execute(query).single())
    assert from_cli == from_python
    assert from_cli["patients"] == 60


def test_recorded_run_obeys_rules_and_is_maximal(built_store):
    assert_run_is_sound(built_store)


def test_read_only_commands_leave_the_store_unchanged(built_store):
    before = fingerprint(built_store)
    for args in (["options"], ["why", "P001"], ["why", "P060"]):
        result = kx(*args, "--store", str(built_store), cwd=built_store.parent)
        assert result.returncode == 0, result.stderr
    assert fingerprint(built_store) == before


def test_whatif_replans_without_the_donor_and_changes_no_file(store_copy):
    with relate.connect(str(store_copy), read_only=True) as conn:
        donor = latest_plan(conn).exchanges[0].donors[0]
    before = fingerprint(store_copy)
    result = kx("whatif", "--withdraw", donor, "--store", str(store_copy), cwd=store_copy.parent)
    assert result.returncode == 0, result.stderr
    assert "breaks" in result.stdout and "rolled back" in result.stdout
    assert {k: v for k, v in fingerprint(store_copy).items() if not k.startswith("wal")} == {
        k: v for k, v in before.items() if not k.startswith("wal")
    }
    with relate.connect(str(store_copy)) as conn:
        what = withdraw_donor(conn, donor)
    assert plan_problems(what.replan, what.replan_inputs) == []
    assert all(donor not in e.donors for e in what.replan.exchanges)
    assert donor not in {a.donor for a in what.replan_inputs.arcs}


@pytest.mark.parametrize(
    "args, code, message",
    [
        (["why", "P999"], 1, "no patient P999"),
        (["whatif", "--withdraw", "D999"], 1, "no donor D999"),
        (["match", "--max-cycle", "4"], 2, "--max-cycle must be 2 or 3"),
        (["options", "--max-cycle", "1"], 2, "--max-cycle must be 2 or 3"),
    ],
)
def test_cli_rejects_bad_input_with_a_clear_message(store_copy, args, code, message):
    result = kx(*args, "--store", str(store_copy), cwd=store_copy.parent)
    assert result.returncode == code
    assert message in result.stderr


def test_build_refuses_to_replace_a_folder_that_is_not_a_store(tmp_path):
    keep = tmp_path / "notes"
    keep.mkdir()
    (keep / "important.txt").write_text("keep me")
    result = kx("build", "--store", str(keep), "--pairs", "5", cwd=tmp_path)
    assert result.returncode == 2 and "not a Relate store" in result.stderr
    assert (keep / "important.txt").read_text() == "keep me"


def test_build_replaces_an_existing_store(tmp_path):
    for _ in range(2):
        result = kx("build", "--store", str(tmp_path / "s"), "--pairs", "5", cwd=tmp_path)
        assert result.returncode == 0, result.stderr


def test_ids_are_case_insensitive(built_store):
    result = kx("why", "p001", "--store", str(built_store), cwd=built_store.parent)
    assert result.returncode == 0 and result.stdout.startswith("P001:")


def test_second_writer_gets_a_clear_message(store_copy):
    with relate.connect(str(store_copy)):
        result = kx("match", "--store", str(store_copy), cwd=store_copy.parent)
    assert result.returncode == 5 and "open for writing by another process" in result.stderr


def test_build_without_relate_cli_keeps_the_existing_store(store_copy, monkeypatch):
    import kidneyloop.store as store_module

    def missing():
        raise RuntimeError("The relate CLI is needed")

    monkeypatch.setattr(store_module, "relate_cli", missing)
    before = fingerprint(store_copy)
    with pytest.raises(RuntimeError):
        store_module.create_json_store(store_copy)
    assert fingerprint(store_copy) == before


def test_commands_on_a_missing_store_say_to_build_first(tmp_path):
    result = kx("match", "--store", str(tmp_path / "nothing"), cwd=tmp_path)
    assert result.returncode == 2 and "kx build" in result.stderr


def test_whatif_before_any_run_says_to_match_first(tmp_path):
    assert kx("build", "--store", str(tmp_path / "s"), "--pairs", "10", cwd=tmp_path).returncode == 0
    result = kx("whatif", "--withdraw", "D001", "--store", str(tmp_path / "s"), cwd=tmp_path)
    assert result.returncode == 1 and "kx match" in result.stderr


@pytest.mark.qa_full
def test_committed_store_run_is_sound():
    store = REPO / "data" / "kidneyloop"
    before = fingerprint(store)
    assert_run_is_sound(store)
    assert fingerprint(store) == before
