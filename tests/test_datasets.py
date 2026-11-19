from pathlib import Path

import pytest
import relate
from typer.testing import CliRunner

from kidneyloop.cli import app
from kidneyloop.datasets import DatasetError, read_preflib
from kidneyloop.runs import run_match
from kidneyloop.store import load_imported

# A hand-made instance in PrefLib's 00036 layout. Pairs 1 and 2 can swap; pair 3's
# O patient has no O donor; pair 4's AB patient is reachable but its AB donor can
# give to nobody, and donor 3 is blood-compatible with patient 4 yet has no arc.
PAIRS = [
    (1, "A", "B", "0", "0.05", 2, 0),
    (2, "B", "A", "0", "0.45", 2, 0),
    (3, "O", "A", "1", "0.2875", 1, 0),
    (4, "AB", "AB", "0", "0.925", 0, 0),
]
ARCS = [(1, 2), (1, 4), (2, 1), (2, 4), (3, 1)]


def write_instance(root: Path, pairs=PAIRS, arcs=ARCS, weights=None, edges=None, alternatives=None) -> tuple[Path, Path]:
    dat = root / "00036-test.dat"
    wmd = root / "00036-test.wmd"
    dat.write_text(
        "Pair,Patient,Donor,Wife-P?,%Pra,Out-Deg,Altruist\n"
        + "".join(",".join(map(str, row)) + "\n" for row in pairs)
    )
    weights = weights or ["1.0"] * len(arcs)
    wmd.write_text(
        "# FILE NAME: 00036-test.wmd\n# DATA TYPE: wmd\n"
        f"# NUMBER ALTERNATIVES: {alternatives if alternatives is not None else len(pairs)}\n"
        f"# NUMBER EDGES: {edges if edges is not None else len(arcs)}\n"
        + "".join(f"# ALTERNATIVE NAME {n}: Pair {n}\n" for n, *_ in pairs)
        + "".join(f"{i},{j},{w}\n" for (i, j), w in zip(arcs, weights))
    )
    return dat, wmd


def test_reads_pairs_and_the_datasets_own_arcs(tmp_path):
    imported = read_preflib(*write_instance(tmp_path))
    assert [p.id for p in imported.pool.patients] == ["P1", "P2", "P3", "P4"]
    assert [(d.id, d.paired_with, d.blood_type) for d in imported.pool.donors][:2] == [("D1", "P1", "B"), ("D2", "P2", "A")]
    assert imported.arcs == (("D1", "P2"), ("D1", "P4"), ("D2", "P1"), ("D2", "P4"), ("D3", "P1"))
    assert imported.source_pra["P4"] == 0.925
    assert imported.source == "PrefLib 00036-test"


def test_ids_are_zero_padded_to_the_pool_size(tmp_path):
    pairs = [(n, "O", "A", "0", "0.05", 0, 0) for n in range(1, 11)]
    imported = read_preflib(*write_instance(tmp_path, pairs=pairs, arcs=[]))
    assert imported.pool.patients[0].id == "P01" and imported.pool.patients[-1].id == "P10"


@pytest.fixture
def imported_graph(tmp_path):
    conn = relate.connect(":memory:")
    load_imported(conn, read_preflib(*write_instance(tmp_path)))
    yield conn
    conn.close()


def test_store_keeps_exactly_the_dataset_arcs(imported_graph):
    arcs = {(r["d"], r["p"]) for r in imported_graph.execute("MATCH (d:Donor)-[:COMPATIBLE]->(p:Patient) RETURN d.id AS d, p.id AS p")}
    assert arcs == {(f"D{i}", f"P{j}") for i, j in ARCS}


def test_pool_pra_counts_blood_compatible_donors_the_dataset_rules_out(imported_graph):
    pra = {r["id"]: r["pra"] for r in imported_graph.execute("MATCH (p:Patient) RETURN p.id AS id, p.pool_pra AS pra")}
    # Only patient 4 has a blood-compatible donor (D3) without an arc: 1 of 4 donors.
    assert pra == {"P1": 0.0, "P2": 0.0, "P3": 0.0, "P4": 0.25}


def test_matching_an_imported_pool_finds_the_only_swap(imported_graph):
    summary = run_match(imported_graph)
    assert [sorted(e.patients) for e in summary.plan.exchanges] == [["P1", "P2"]]


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (dict(pairs=PAIRS[:3] + [(4, "AB", "AB", "0", "0.925", 0, 1)]), "altruistic donor"),
        (dict(arcs=ARCS[:4] + [(3, 4), (4, 3)]), "ABO rules forbid"),
        (dict(arcs=ARCS[:4] + [(3, 3)]), "their own patient"),
        (dict(arcs=ARCS[:4] + [(3, 9)]), "not in"),
        (dict(arcs=ARCS[:4] + [(2, 1)]), "appears twice"),
        (dict(arcs=ARCS + [(3, 4)]), "Out-Deg"),
        (dict(weights=["1.0"] * 4 + ["0.0"]), "arc weight 0.0"),
        (dict(edges=99), "NUMBER EDGES"),
        (dict(alternatives=3), "NUMBER ALTERNATIVES"),
        (dict(pairs=PAIRS[:3] + [(4, "Z", "AB", "0", "0.925", 0, 0)]), "unknown blood type"),
        (dict(pairs=PAIRS[:3] + [(4, "AB", "AB", "0", "1.5", 0, 0)]), "outside 0 to 1"),
        (dict(pairs=PAIRS[:3] + [(5, "AB", "AB", "0", "0.9", 0, 0)]), "numbered 1 to 4"),
    ],
)
def test_rejects_files_it_cannot_import_faithfully(tmp_path, change, message):
    with pytest.raises(DatasetError, match=message):
        read_preflib(*write_instance(tmp_path, **change))


def test_rejects_an_unexpected_header(tmp_path):
    dat, wmd = write_instance(tmp_path)
    dat.write_text("Pair,Patient,Donor\n1,A,B\n")
    with pytest.raises(DatasetError, match="expected columns"):
        read_preflib(dat, wmd)


def test_cli_reports_a_bad_file_without_creating_a_store(tmp_path):
    dat, _ = write_instance(tmp_path, arcs=ARCS[:4] + [(3, 4), (4, 3)])
    store = tmp_path / "store"
    result = CliRunner().invoke(app, ["import-preflib", str(dat), "--store", str(store)])
    assert result.exit_code == 2
    assert "ABO rules forbid" in result.output
    assert not store.exists()


def test_cli_reports_a_missing_file(tmp_path):
    result = CliRunner().invoke(app, ["import-preflib", str(tmp_path / "nope.dat"), "--store", str(tmp_path / "s")])
    assert result.exit_code == 2
    assert "Cannot read" in result.output


def test_why_names_the_dataset_instead_of_antibodies(imported_graph):
    from kidneyloop.explain import explain, render

    lines = render(explain(imported_graph, "P4"))
    assert lines[0] == "P4: blood type AB; the PrefLib 00036-test arcs rule out 25% of this pool's donors (pool PRA)"
    assert not any("ntibod" in line for line in lines)


def test_why_reason_blames_missing_arcs_not_antigens(tmp_path):
    from kidneyloop.explain import explain, render

    conn = relate.connect(":memory:")
    load_imported(conn, read_preflib(*write_instance(tmp_path, arcs=[(1, 2), (2, 1), (3, 1)], pairs=[(1, "A", "B", "0", "0.05", 1, 0), (2, "B", "A", "0", "0.45", 1, 0), *PAIRS[2:]])))
    lines = render(explain(conn, "P4"))
    conn.close()
    assert lines[-1] == "Reason: PrefLib 00036-test lists no arc from any blood-compatible donor."
