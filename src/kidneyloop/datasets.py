"""Read published kidney-exchange benchmark pools into KidneyLoop's model.

Benchmark files give the compatibility graph itself (which donor can give to which
patient), not the HLA typing behind it, so an imported pool carries its arcs.
"""

import csv
from dataclasses import dataclass
from pathlib import Path

from .model import ABO_CAN_GIVE_TO, BLOOD_TYPES, Donor, Patient, Pool


class DatasetError(ValueError):
    pass


@dataclass(frozen=True)
class ImportedPool:
    pool: Pool
    arcs: tuple[tuple[str, str], ...]
    source: str
    source_pra: dict[str, float]


PREFLIB_COLUMNS = ["Pair", "Patient", "Donor", "Wife-P?", "%Pra", "Out-Deg", "Altruist"]


def read_preflib(dat: Path, wmd: Path) -> ImportedPool:
    """A PrefLib kidney instance (dataset 00036): a .dat table of pairs and a .wmd arc list.

    In the .wmd file vertex i is pair i, and an arc i,j means pair i's donor can give
    to pair j's patient. Files with altruistic donors are rejected: PrefLib marks them
    as faulty, and the ones checked here have arcs that break the blood-type rules.
    """
    with dat.open(newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != PREFLIB_COLUMNS:
            raise DatasetError(f"{dat.name}: expected columns {','.join(PREFLIB_COLUMNS)}, got {reader.fieldnames}")
        rows = list(reader)
    width = len(str(len(rows)))
    pairs: dict[int, tuple[Patient, Donor, float]] = {}
    for line, row in enumerate(rows, start=2):
        n = _int(row["Pair"], dat, line)
        if row["Altruist"] != "0":
            raise DatasetError(
                f"{dat.name}:{line}: pair {n} is an altruistic donor; PrefLib marks its altruist files as faulty, so they are not imported"
            )
        patient_blood, donor_blood = row["Patient"], row["Donor"]
        if patient_blood not in BLOOD_TYPES or donor_blood not in BLOOD_TYPES:
            raise DatasetError(f"{dat.name}:{line}: unknown blood type in {patient_blood!r}, {donor_blood!r}")
        if n in pairs:
            raise DatasetError(f"{dat.name}:{line}: pair {n} appears twice")
        patient = Patient(f"P{n:0{width}d}", patient_blood, frozenset(), 0)
        donor = Donor(f"D{n:0{width}d}", donor_blood, frozenset(), patient.id)
        pairs[n] = (patient, donor, _fraction(row["%Pra"], dat, line))
    if sorted(pairs) != list(range(1, len(pairs) + 1)):
        raise DatasetError(f"{dat.name}: pairs must be numbered 1 to {len(pairs)}")

    header, arcs = _read_wmd(wmd)
    if header.get("NUMBER ALTERNATIVES") != str(len(pairs)):
        raise DatasetError(f"{wmd.name}: NUMBER ALTERNATIVES is {header.get('NUMBER ALTERNATIVES')}, but {dat.name} has {len(pairs)} pairs")
    if header.get("NUMBER EDGES") != str(len(arcs)):
        raise DatasetError(f"{wmd.name}: NUMBER EDGES is {header.get('NUMBER EDGES')}, but the file lists {len(arcs)} arcs")
    out_degree = {n: 0 for n in pairs}
    seen: set[tuple[int, int]] = set()
    edges: list[tuple[str, str]] = []
    for line, (i, j) in arcs:
        if i not in pairs or j not in pairs:
            raise DatasetError(f"{wmd.name}:{line}: arc {i},{j} names a pair that is not in {dat.name}")
        if i == j:
            raise DatasetError(f"{wmd.name}:{line}: pair {i}'s donor is listed as compatible with their own patient")
        if (i, j) in seen:
            raise DatasetError(f"{wmd.name}:{line}: arc {i},{j} appears twice")
        donor, patient = pairs[i][1], pairs[j][0]
        if patient.blood_type not in ABO_CAN_GIVE_TO[donor.blood_type]:
            raise DatasetError(
                f"{wmd.name}:{line}: arc {i},{j} gives blood type {donor.blood_type} to {patient.blood_type}, which the ABO rules forbid"
            )
        seen.add((i, j))
        out_degree[i] += 1
        edges.append((donor.id, patient.id))
    for line, row in enumerate(rows, start=2):
        n = int(row["Pair"])
        if out_degree[n] != _int(row["Out-Deg"], dat, line):
            raise DatasetError(f"{dat.name}:{line}: pair {n} has Out-Deg {row['Out-Deg']}, but {wmd.name} lists {out_degree[n]} arcs")

    ordered = [pairs[n] for n in sorted(pairs)]
    return ImportedPool(
        pool=Pool(tuple(p for p, _, _ in ordered), tuple(d for _, d, _ in ordered)),
        arcs=tuple(edges),
        source=f"PrefLib {dat.stem}",
        source_pra={p.id: pra for p, _, pra in ordered},
    )


def _read_wmd(path: Path) -> tuple[dict[str, str], list[tuple[int, tuple[int, int]]]]:
    header: dict[str, str] = {}
    arcs: list[tuple[int, tuple[int, int]]] = []
    with path.open() as f:
        for line, text in enumerate(f, start=1):
            text = text.strip()
            if not text:
                continue
            if text.startswith("#"):
                key, _, value = text[1:].partition(":")
                header[key.strip()] = value.strip()
                continue
            parts = text.split(",")
            if len(parts) != 3:
                raise DatasetError(f"{path.name}:{line}: expected 'from,to,weight', got {text!r}")
            # The solver gives every transplant the same base value, so a weight
            # other than 1 would be silently ignored; refuse it instead.
            if parts[2] != "1.0":
                raise DatasetError(f"{path.name}:{line}: arc weight {parts[2]} is not 1.0, and KidneyLoop does not use arc weights")
            arcs.append((line, (_int(parts[0], path, line), _int(parts[1], path, line))))
    return header, arcs


def _int(text: str, path: Path, line: int) -> int:
    try:
        return int(text)
    except ValueError:
        raise DatasetError(f"{path.name}:{line}: {text!r} is not a whole number") from None


def _fraction(text: str, path: Path, line: int) -> float:
    try:
        value = float(text)
    except ValueError:
        raise DatasetError(f"{path.name}:{line}: {text!r} is not a number") from None
    if not 0.0 <= value <= 1.0:
        raise DatasetError(f"{path.name}:{line}: PRA {value} is outside 0 to 1")
    return value
