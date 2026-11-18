import os
import subprocess
import sys

import relate

from kidneyloop.model import Pool
from kidneyloop.runs import check_inputs, input_sha256, run_match, run_objective, run_record
from kidneyloop.store import derive, load_pool

from pool_builders import donor, patient
from test_runs import swap_pool


def test_unchanged_store_matches_the_recorded_hash(graph_of):
    conn = graph_of(*swap_pool())
    run_match(conn)
    check = check_inputs(conn, "R1")
    assert len(check.stored) == 64
    assert check.unchanged and check.warning() is None


def test_removing_a_compatible_edge_changes_the_hash(graph_of):
    conn = graph_of(*swap_pool())
    run_match(conn)
    conn.execute("MATCH (:Donor {id: 'D1'})-[c:COMPATIBLE]->(:Patient {id: 'P3'}) DELETE c")
    check = check_inputs(conn, "R1")
    assert not check.unchanged
    assert "pool changed since R1" in check.warning()


def test_a_new_wait_days_changes_the_hash(graph_of):
    conn = graph_of(*swap_pool())
    run_match(conn)
    conn.execute("MATCH (p:Patient {id: 'P2'}) SET p.wait_days = p.wait_days + 1")
    assert not check_inputs(conn, "R1").unchanged


def test_hash_covers_the_run_settings(graph_of):
    conn = graph_of(*swap_pool())
    digests = {input_sha256(conn, c, k, "x", o) for c in (2, 3) for k in (3, 4) for o in ("count", "expected")}
    assert len(digests) == 8


def test_hash_ignores_the_order_the_pool_was_loaded_in():
    patients, donors = swap_pool()
    digests = set()
    for order in (1, -1):
        conn = relate.connect(":memory:")
        load_pool(conn, Pool(tuple(patients[::order]), tuple(donors[::order])))
        derive(conn)
        digests.add(input_sha256(conn, 3, 4, "x", "count"))
        conn.close()
    assert len(digests) == 1


PROGRAM = """
import relate
from kidneyloop.generator import generate_pool
from kidneyloop.runs import input_sha256
from kidneyloop.store import derive, load_pool
conn = relate.connect(":memory:")
load_pool(conn, generate_pool(seed=7, pairs=40, altruists=3))
derive(conn)
print(input_sha256(conn, 3, 4, "x", "count"))
"""


def test_hash_is_the_same_in_every_process():
    digests = {
        subprocess.run(
            [sys.executable, "-c", PROGRAM], env={**os.environ, "PYTHONHASHSEED": seed}, capture_output=True, text=True, check=True
        ).stdout
        for seed in ("0", "1", "2")
    }
    assert len(digests) == 1


def test_run_records_its_objective_kind_and_older_runs_read_from_text(graph_of):
    conn = graph_of([patient("P1"), patient("P2")], [donor("D1", paired_with="P1"), donor("D2", paired_with="P2")])
    run_match(conn, objective="expected")
    assert run_record(conn, "R1")["objective_kind"] == "expected"
    assert run_objective({"objective": "max transplants; tie-break 0.7 x pool PRA + 0.3 x wait share"}) == "count"


def test_run_without_a_hash_says_it_cannot_check(graph_of):
    conn = graph_of(*swap_pool())
    run_match(conn)
    conn.execute("MATCH (r:MatchRun) REMOVE r.input_sha256")
    check = check_inputs(conn, "R1")
    assert check.stored is None and "cannot be detected" in check.warning()
