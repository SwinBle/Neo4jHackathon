import itertools
import random
import shutil
from pathlib import Path

import pytest
import relate
from typer.testing import CliRunner

import kidneyloop.cli as cli
from kidneyloop.cpra import CpraDataError, HaplotypeTable, cpra, read_haplotypes, serology_map
from kidneyloop.model import ANTIGENS, Donor, Patient
from kidneyloop.policy import load_policy
from kidneyloop.runs import input_sha256, read_inputs
from kidneyloop.store import set_cpra

REPO = Path(__file__).resolve().parents[1]
CPRA_POLICY = REPO / "policies" / "default-cpra.json"

REL_DNA_SER = """# file: rel_dna_ser.txt
A*;02:01:01:01;2;;;
A*;02:01:01:02L;;0/2;;
A*;02:01:02;2;;;
A*;01:01:01:02N;0;;;
B*;40:01:01;60;;;
B*;15:01:01:01;62;;;
B*;15:01:02;;;75;
DRB1*;03:01:01:01;17;;;
DRB1*;14:54:01:01;14;;;
C*;01:02:01:01;1;;;
"""


def test_serology_uses_expressed_alleles_only():
    sero = serology_map(REL_DNA_SER)
    assert sero["A*02:01"] == "A2"
    assert sero["B*40:01"] == "B60"
    assert sero["DRB1*03:01"] == "DR17"
    assert sero["DRB1*14:54"] == "DR14"
    assert "A*01:01" not in sero
    assert "C*01:02" not in sero


def test_an_allele_with_two_antigens_maps_to_none():
    assert serology_map(REL_DNA_SER)["B*15:01"] is None


def test_null_serology_maps_to_none():
    assert serology_map("A*;03:01:01:01;0;;;\n")["A*03:01"] is None


def test_rejects_short_lines():
    with pytest.raises(CpraDataError, match="6 fields"):
        serology_map("A*;02:01\n")


HAPLOTYPES = """Haplo,Count,Freq
A*02:01~C*01:02~B*40:01~DRB3*01:01~DRB1*03:01~DQB1*02:01~DPB1*04:01,1,0.5
A*02:01~C*01:02~B*40:01~DRB4*01:01~DRB1*03:01~DQB1*02:01~DPB1*02:01,1,0.2
A*02:01~C*01:02~B*15:01~DRB1*14:54~DQB1*05:03~DPB1*04:01,1,0.3
"""


def test_haplotypes_merge_by_antigens_and_report_unmapped_mass():
    table = read_haplotypes(HAPLOTYPES, "TEST", serology_map(REL_DNA_SER))
    by = dict(table.haplotypes)
    assert by[frozenset({"A2", "B60", "DR17"})] == pytest.approx(0.7)
    assert by[frozenset({"A2", "DR14"})] == pytest.approx(0.3)
    assert table.unmapped == pytest.approx(0.3)


def test_cpra_follows_hardy_weinberg():
    table = read_haplotypes(HAPLOTYPES, "TEST", serology_map(REL_DNA_SER))
    assert cpra(frozenset(), table) == 0.0
    assert cpra(frozenset({"B60"}), table) == pytest.approx(1 - 0.3**2)
    assert cpra(frozenset({"A2"}), table) == pytest.approx(1.0)


def test_rejects_frequencies_that_do_not_sum_to_one():
    with pytest.raises(CpraDataError, match="sum to"):
        read_haplotypes("Haplo,Count,Freq\nA*02:01~B*40:01~DRB1*03:01,1,0.5\n", "TEST", serology_map(REL_DNA_SER))


def test_a_sum_slightly_off_one_is_scaled_so_no_antibodies_score_zero():
    text = "Haplo,Count,Freq\nA*02:01~B*40:01~DRB1*03:01,1,0.4996\nA*02:01~DRB1*14:54,1,0.4998\n"
    table = read_haplotypes(text, "TEST", serology_map(REL_DNA_SER))
    assert sum(f for _, f in table.haplotypes) == pytest.approx(1.0, abs=1e-12)
    assert cpra(frozenset(), table) == pytest.approx(0.0, abs=1e-12)


def test_rejects_unexpected_columns():
    with pytest.raises(CpraDataError, match="expected columns"):
        read_haplotypes("Haplotype,Freq\n", "TEST", {})


@pytest.mark.parametrize("seed", range(20))
def test_closed_form_equals_the_genotype_sum_optn_uses(seed):
    """OPTN's CPRA is the sum of genotype frequency times [genotype has an unacceptable antigen]."""
    rng = random.Random(seed)
    sets = [frozenset(rng.sample(ANTIGENS, 3)) for _ in range(6)]
    weights = [rng.random() for _ in sets]
    table = HaplotypeTable("TEST", tuple((s, w / sum(weights)) for s, w in zip(sets, weights)), 0.0)
    unacceptable = frozenset(rng.sample(ANTIGENS, rng.randint(0, 6)))
    genotype_sum = sum(
        fi * fj
        for (hi, fi), (hj, fj) in itertools.product(table.haplotypes, repeat=2)
        if (hi | hj) & unacceptable
    )
    assert cpra(unacceptable, table) == pytest.approx(genotype_sum)


TABLE = HaplotypeTable("TEST", ((frozenset({"A2", "B60"}), 0.6), (frozenset({"A1", "B8"}), 0.4)), 0.05)


def two_pair_graph(graph_of):
    patients = [Patient("P1", "O", frozenset({"A2"}), 100), Patient("P2", "A", frozenset(), 50)]
    donors = [Donor("D1", "O", frozenset({"A1"}), "P1"), Donor("D2", "O", frozenset({"A2"}), "P2")]
    return graph_of(patients, donors)


def test_set_cpra_stores_values_and_replaces_the_reference(graph_of):
    conn = two_pair_graph(graph_of)
    set_cpra(conn, TABLE, "freqs", "sero")
    set_cpra(conn, TABLE, "freqs", "sero")
    values = {r["id"]: r["c"] for r in conn.execute("MATCH (p:Patient) RETURN p.id AS id, p.cpra AS c")}
    assert values == {"P1": pytest.approx(1 - 0.4**2), "P2": 0.0}
    assert conn.execute("MATCH (c:CpraReference) RETURN count(c) AS n, max(c.unmapped) AS u").single() == {"n": 1, "u": 0.05}


def test_set_cpra_refuses_an_imported_pool(graph_of):
    conn = two_pair_graph(graph_of)
    conn.execute("CREATE (:Dataset {name: 'PrefLib test'})")
    with pytest.raises(ValueError, match="without HLA typing"):
        set_cpra(conn, TABLE, "freqs", "sero")


def test_cpra_policy_reads_cpra_and_needs_it(graph_of):
    conn = two_pair_graph(graph_of)
    policy = load_policy(CPRA_POLICY)
    with pytest.raises(LookupError, match="run `kx cpra` first"):
        read_inputs(conn, 3, policy=policy)
    set_cpra(conn, TABLE, "freqs", "sero")
    # P1's CPRA is 84%: the top crossmatch band (50%) plus 8% exogenous failure.
    assert read_inputs(conn, 3, policy=policy).success["P1"] == pytest.approx(1 - 0.5 - 0.08)
    assert read_inputs(conn, 3).success["P1"] == pytest.approx(1 - 0.35 - 0.08)


def test_input_hash_changes_only_once_cpra_is_set(graph_of):
    conn = two_pair_graph(graph_of)
    before = input_sha256(conn, 3, 4, "policy", "count")
    assert input_sha256(conn, 3, 4, "policy", "count") == before
    set_cpra(conn, TABLE, "freqs", "sero")
    assert input_sha256(conn, 3, 4, "policy", "count") != before


def test_cli_writes_cpra_into_a_copy_of_the_committed_store(tmp_path, monkeypatch):
    store = tmp_path / "store"
    shutil.copytree(REPO / "data" / "kidneyloop", store)
    monkeypatch.setattr(cli, "load_reference", lambda cache, population: TABLE)
    result = CliRunner().invoke(cli.app, ["cpra", "--store", str(store), "--population", "TEST"])
    assert result.exit_code == 0, result.output
    assert "CPRA for 300 patients" in result.output
    assert "counted as safe" in result.output
    conn = relate.connect(str(store), read_only=True)
    assert conn.execute("MATCH (p:Patient) WHERE p.cpra IS NULL RETURN count(p) AS n").single()["n"] == 0
    conn.close()


def test_cli_match_explains_a_missing_cpra(tmp_path):
    store = tmp_path / "store"
    shutil.copytree(REPO / "data" / "kidneyloop", store)
    result = CliRunner().invoke(cli.app, ["match", "--store", str(store), "--policy", str(CPRA_POLICY)])
    assert result.exit_code == 2
    assert "run `kx cpra` first" in result.output


def test_why_shows_cpra_once_set(graph_of):
    from kidneyloop.explain import explain, render

    conn = two_pair_graph(graph_of)
    assert "CPRA" not in render(explain(conn, "P1"))[0]
    set_cpra(conn, TABLE, "freqs", "sero")
    assert render(explain(conn, "P1"))[0].endswith("; CPRA 84% against NMDP TEST haplotype frequencies")
