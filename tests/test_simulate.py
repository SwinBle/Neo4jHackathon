import json

import relate

from kidneyloop.generator import generate_pool
from kidneyloop.policy import DEFAULT_POLICY_FILE, load_policy, parse_policy
from kidneyloop.simulate import render, simulate
from kidneyloop.store import derive, load_pool

from pool_builders import donor, patient

COUNTS = "MATCH (n) WITH count(n) AS nodes MATCH ()-[r]->() RETURN nodes, count(r) AS rels"


def even_odds_policy():
    data = json.loads(DEFAULT_POLICY_FILE.read_text())
    data["success"]["crossmatch_failure"] = [{"from_percent": 0, "value": 0.5}]
    data["success"]["exogenous_failure"] = 0.0
    return parse_policy(data, "test")


def only_arcs(conn, arcs):
    conn.execute("MATCH ()-[c:COMPATIBLE]->() DELETE c")
    for d, p in arcs:
        conn.execute(f"MATCH (d:Donor {{id: '{d}'}}), (p:Patient {{id: '{p}'}}) CREATE (d)-[:COMPATIBLE]->(p)")


def test_a_chain_stops_at_its_first_failed_transplant(graph_of):
    conn = graph_of([patient("P1"), patient("P2")], [donor("N1"), donor("D1", paired_with="P1"), donor("D2", paired_with="P2")])
    only_arcs(conn, [("N1", "P1"), ("D1", "P2")])
    sim = simulate(conn, even_odds_policy(), trials=4000, seed=1)
    realized = sim.arm("count").realized
    # 0.5 for the first transplant plus 0.25 for the second, which needs the first.
    assert abs(realized.mean() - 0.75) < 0.05
    assert set(realized) == {0, 1, 2}


def test_a_cycle_happens_only_if_every_transplant_does(graph_of):
    conn = graph_of([patient("P1"), patient("P2")], [donor("D1", paired_with="P1"), donor("D2", paired_with="P2")])
    only_arcs(conn, [("D1", "P2"), ("D2", "P1")])
    sim = simulate(conn, even_odds_policy(), trials=4000, seed=1)
    realized = sim.arm("count").realized
    assert abs(realized.mean() - 0.5) < 0.05
    assert set(realized) == {0, 2}


def test_simulation_is_reproducible_read_only_and_agrees_with_the_exact_expectation():
    conn = relate.connect(":memory:")
    load_pool(conn, generate_pool(seed=5, pairs=40, altruists=2))
    derive(conn)
    before = conn.execute(COUNTS).single()
    first = simulate(conn, load_policy(), trials=3000, seed=9)
    again = simulate(conn, load_policy(), trials=3000, seed=9)
    assert conn.execute(COUNTS).single() == before
    assert render(first) == render(again)
    for arm in first.arms:
        assert abs(arm.realized.mean() - arm.expected) < 4 * arm.realized.std() / len(arm.realized) ** 0.5 + 1e-9
        assert sum(b.sum() for b in arm.realized_by_band) == arm.realized.sum()
    assert sum(first.band_patients) == 40
    conn.close()
