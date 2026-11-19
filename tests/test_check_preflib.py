import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("check_preflib", REPO / "scripts" / "check_preflib.py")
check_preflib = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_preflib)


def test_counts_each_cycle_once_and_both_directions_of_a_triple():
    # Every pair of 1, 2, 3 can swap, and the triple can go round either way.
    arcs = [(1, 2), (2, 1), (2, 3), (3, 2), (1, 3), (3, 1)]
    assert check_preflib.count_cycles(arcs) == (3, 2)


def test_a_one_way_triangle_is_one_three_cycle():
    assert check_preflib.count_cycles([(3, 1), (1, 2), (2, 3), (1, 4)]) == (0, 1)
