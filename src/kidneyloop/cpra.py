"""CPRA: the share of a reference donor population a patient's antibodies rule out.

OPTN computes CPRA = sum over donor genotypes of frequency x [genotype carries an
unacceptable antigen]. With haplotype frequencies f and Hardy-Weinberg genotypes that
sum is 1 - (sum of f over haplotypes with no unacceptable antigen)^2, which is what
`cpra` computes. Like OPTN's CPRA it covers HLA only; blood type is handled by the
ABO rules.

Neither source file may be redistributed in modified form (NMDP: CC BY-NC-ND 4.0;
IPD-IMGT/HLA: CC BY-ND), so both are downloaded at run time and nothing derived from
them is committed.
"""

import csv
import hashlib
import io
import re
import tarfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .model import ANTIGENS

NMDP_URL = "https://zenodo.org/records/4474442/files/freqs_7loc_2020_trim.tar.gz?download=1"
NMDP_MD5 = "2ff384cc2f57cde3da73566616f6aa4c"
NMDP_RECORD = "NMDP 7-locus haplotype frequencies (2020, trimmed), Zenodo record 4474442"
# Release 3.60.0. Release 3.66.0 writes values such as 4001 in the serology column
# where 3.60.0 gives the antigen (B60), so the column's meaning changed and the
# release is pinned.
SEROLOGY_URL = "https://raw.githubusercontent.com/ANHIG/IMGTHLA/3600/wmda/rel_dna_ser.txt"
SEROLOGY_RELEASE = "IPD-IMGT/HLA 3.60.0 rel_dna_ser.txt"
DEFAULT_POPULATION = "SCSEAI"

LOCUS_PREFIX = {"A*": "A", "B*": "B", "DRB1*": "DR"}
EXPRESSION_SUFFIX = re.compile(r"[NLSQCA]$")


class CpraDataError(ValueError):
    pass


@dataclass(frozen=True)
class HaplotypeTable:
    """A-B-DR haplotypes as KidneyLoop antigen names with their population frequency.

    unmapped is the frequency of haplotypes with an allele that has no single
    serologic antigen. That allele is left out and the haplotype keeps its other
    antigens, so CPRA can only be underestimated by them.
    """

    population: str
    haplotypes: tuple[tuple[frozenset[str], float], ...]
    unmapped: float


def serology_map(rel_dna_ser: str) -> dict[str, str | None]:
    """Two-field allele name (A*02:01) to its antigen (A2), or None for a null or unclear allele.

    rel_dna_ser.txt lines read locus;allele;unambiguous;possible;assumed;exceptions.
    Only alleles with normal expression count, since an NMDP two-field name without a
    suffix names the expressed allele.
    """
    values: dict[str, set[str]] = {}
    for line in rel_dna_ser.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        fields = line.split(";")
        if len(fields) < 6:
            raise CpraDataError(f"serology file: expected 6 fields, got {line!r}")
        locus, allele = fields[0], fields[1]
        if locus not in LOCUS_PREFIX or EXPRESSION_SUFFIX.search(allele):
            continue
        two_field = locus + ":".join(allele.split(":")[:2])
        antigen = next((v for v in fields[2:6] if v), "")
        values.setdefault(two_field, set()).add(antigen)
    out: dict[str, str | None] = {}
    for name, found in values.items():
        prefix = LOCUS_PREFIX[name.split("*")[0] + "*"]
        named = {f"{prefix}{v}" for v in found if v and v != "0" and v.isdigit()}
        out[name] = named.pop() if len(named) == 1 else None
    return out


def read_haplotypes(csv_text: str, population: str, serology: dict[str, str | None]) -> HaplotypeTable:
    reader = csv.DictReader(io.StringIO(csv_text))
    if reader.fieldnames != ["Haplo", "Count", "Freq"]:
        raise CpraDataError(f"{population}: expected columns Haplo,Count,Freq, got {reader.fieldnames}")
    merged: dict[frozenset[str], float] = {}
    unmapped = total = 0.0
    for row in reader:
        freq = float(row["Freq"])
        total += freq
        antigens: set[str] = set()
        clear = True
        for allele in row["Haplo"].split("~"):
            if allele.split("*")[0] + "*" not in LOCUS_PREFIX:
                continue
            antigen = serology.get(allele)
            if antigen is None:
                clear = False
            else:
                antigens.add(antigen)
        if not clear:
            unmapped += freq
        key = frozenset(antigens & set(ANTIGENS))
        merged[key] = merged.get(key, 0.0) + freq
    if not 0.999 <= total <= 1.001:
        raise CpraDataError(f"{population}: haplotype frequencies sum to {total}, not 1")
    # Rounding in the published file leaves the sum a little off 1; scale it back so
    # a patient with no antibodies scores exactly 0.
    return HaplotypeTable(
        population, tuple(sorted(((k, f / total) for k, f in merged.items()), key=lambda kv: sorted(kv[0]))), unmapped / total
    )


def cpra(antibodies: frozenset[str], table: HaplotypeTable) -> float:
    clear = sum(f for antigens, f in table.haplotypes if not antigens & antibodies)
    return max(0.0, 1.0 - clear * clear)


def _download(url: str, target: Path) -> Path:
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(url, timeout=120) as response:
            target.write_bytes(response.read())
    return target


def load_reference(cache: Path, population: str = DEFAULT_POPULATION) -> HaplotypeTable:
    """Download (once) and read the NMDP frequencies for a population, mapped to antigens."""
    archive = _download(NMDP_URL, cache / "freqs_7loc_2020_trim.tar.gz")
    if hashlib.md5(archive.read_bytes()).hexdigest() != NMDP_MD5:
        raise CpraDataError(f"{archive} does not match the md5 Zenodo publishes; delete it to download again")
    serology = serology_map(_download(SEROLOGY_URL, cache / "rel_dna_ser-3.60.0.txt").read_text(encoding="utf-8"))
    with tarfile.open(archive) as tar:
        names = {Path(m.name).name: m for m in tar.getmembers() if m.isfile()}
        member = names.get(f"freqs.{population}.csv")
        if member is None:
            known = sorted(n.removeprefix("freqs.").removesuffix(".csv") for n in names)
            raise CpraDataError(f"no population {population!r}; the file has {', '.join(known)}")
        text = tar.extractfile(member).read().decode("utf-8")
    return read_haplotypes(text, population, serology)
