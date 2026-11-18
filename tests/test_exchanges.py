from kidneyloop.exchanges import Arc, Exchange, find_arcs, find_cycles

from pool_builders import donor, patient


def pairs(n):
    return [patient(f"P{i}") for i in range(1, n + 1)], [donor(f"D{i}", paired_with=f"P{i}") for i in range(1, n + 1)]


def test_two_pairs_that_can_swap_form_one_cycle(graph_of):
    patients, donors = pairs(2)
    conn = graph_of(patients, donors)
    assert find_cycles(conn, max_length=2) == [Exchange("cycle", ("D2", "D1"), ("P1", "P2"))]


def test_three_way_cycle_is_reported_once_not_per_rotation(graph_of):
    patients = [patient("P1", blood="A", antibodies=["B8"]), patient("P2", blood="B"), patient("P3", blood="O")]
    donors = [
        donor("D1", blood="B", paired_with="P1"),
        donor("D2", blood="O", antigens=["B8"], paired_with="P2"),
        donor("D3", blood="A", paired_with="P3"),
    ]
    conn = graph_of(patients, donors)
    cycles = find_cycles(conn, max_length=3)
    assert cycles == [Exchange("cycle", ("D3", "D1", "D2"), ("P1", "P2", "P3"))]


def test_each_patient_in_a_cycle_receives_from_a_compatible_donor(graph_of):
    patients, donors = pairs(3)
    conn = graph_of(patients, donors)
    arcs = {(a.donor, a.patient) for a in find_arcs(conn)}
    for cycle in find_cycles(conn, max_length=3):
        assert all((d, p) in arcs for d, p in zip(cycle.donors, cycle.patients))


def test_blocked_arc_breaks_the_cycle(graph_of):
    patients = [patient("P1", antibodies=["B8"]), patient("P2")]
    donors = [donor("D1", paired_with="P1"), donor("D2", antigens=["B8"], paired_with="P2")]
    conn = graph_of(patients, donors)
    assert find_cycles(conn) == []


def test_arcs_mark_altruistic_donors(graph_of):
    conn = graph_of([patient("P1")], [donor("N1")])
    assert find_arcs(conn) == [Arc("N1", "P1", True)]
