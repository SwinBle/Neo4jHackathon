import relate

from kidneyloop.generator import generate_pool
from kidneyloop.model import can_donate
from kidneyloop.store import derive, load_pool

from conftest import donor, patient

EDGES = "MATCH (d:Donor)-[:COMPATIBLE]->(p:Patient) RETURN d.id AS d, p.id AS p"


def edges(conn):
    return {(r["d"], r["p"]) for r in conn.execute(EDGES)}


def test_o_donor_gives_to_a_patient_but_a_donor_not_to_o_patient(graph_of):
    conn = graph_of(
        [patient("PA", blood="A"), patient("PO", blood="O")],
        [donor("DO", blood="O"), donor("DA", blood="A")],
    )
    assert ("DO", "PA") in edges(conn)
    assert ("DA", "PO") not in edges(conn)


def test_antibody_to_donor_antigen_blocks_the_edge(graph_of):
    conn = graph_of(
        [patient("P1", antibodies=["B8"]), patient("P2")],
        [donor("D1", antigens=["A2", "B8"])],
    )
    assert edges(conn) == {("D1", "P2")}


def test_donor_never_gets_an_edge_to_own_patient(graph_of):
    conn = graph_of([patient("P1")], [donor("D1", paired_with="P1")])
    assert edges(conn) == set()


def test_cpra_is_share_of_pool_donors_blocked_by_antibodies(graph_of):
    conn = graph_of(
        [patient("P1", antibodies=["B8"])],
        [donor("D1", antigens=["B8"]), donor("D2", antigens=["A2"]), donor("D3", antigens=["B8", "A2"]), donor("D4")],
    )
    assert conn.execute("MATCH (p:Patient {id: 'P1'}) RETURN p.cpra AS c").single()["c"] == 0.5


def test_generated_pairs_are_incompatible_and_reproducible():
    pool = generate_pool(seed=7, pairs=40, altruists=3)
    assert pool == generate_pool(seed=7, pairs=40, altruists=3)
    by_id = {p.id: p for p in pool.patients}
    paired = [d for d in pool.donors if d.paired_with]
    assert len(paired) == 40
    assert not any(can_donate(d, by_id[d.paired_with]) for d in paired)
    assert sum(d.paired_with is None for d in pool.donors) == 3


def test_graph_rule_matches_reference_rule_on_generated_pool():
    pool = generate_pool(seed=7, pairs=40, altruists=3)
    conn = relate.connect(":memory:")
    load_pool(conn, pool)
    derive(conn)
    expected = {
        (d.id, p.id) for d in pool.donors for p in pool.patients if d.paired_with != p.id and can_donate(d, p)
    }
    assert edges(conn) == expected
    conn.close()
