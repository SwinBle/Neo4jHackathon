from dataclasses import dataclass
from typing import Literal

import relate


@dataclass(frozen=True)
class Exchange:
    """One feasible exchange, as the ordered donors and patients of its transplants.

    donors[i] gives a kidney to patients[i]. In a cycle, each patient's paired donor
    gives to the next patient and the last pair's donor gives to the first patient.
    A chain starts at an altruistic donor and leaves its last paired donor free.
    """

    kind: Literal["cycle", "chain"]
    donors: tuple[str, ...]
    patients: tuple[str, ...]

    @property
    def size(self) -> int:
        return len(self.patients)


def _cycle_query(length: int) -> str:
    """Cycles whose first patient has the smallest id, so each cycle appears once.

    One MATCH per hop lets each WHERE prune before the next hop expands; a single
    long pattern with the filters at the end measured about twice as slow on the
    300-pair store.
    """
    stages = [
        "MATCH (d1:Donor)-[:PAIRED_WITH]->(p1:Patient) WHERE p1.id IN $first "
        "MATCH (d1)-[:COMPATIBLE]->(p2:Patient) WHERE p1.id < p2.id"
    ]
    for i in range(2, length):
        earlier = " AND ".join(f"p{i + 1} <> p{j}" for j in range(2, i + 1))
        stages.append(
            f"MATCH (d{i}:Donor)-[:PAIRED_WITH]->(p{i}), (d{i})-[:COMPATIBLE]->(p{i + 1}:Patient) "
            f"WHERE p1.id < p{i + 1}.id AND {earlier}"
        )
    stages.append(f"MATCH (d{length}:Donor)-[:PAIRED_WITH]->(p{length}), (d{length})-[:COMPATIBLE]->(p1)")
    donors = ", ".join(f"d{i}.id" for i in [length, *range(1, length)])
    patients = ", ".join(f"p{i}.id" for i in range(1, length + 1))
    return " ".join(stages) + f" RETURN [{donors}] AS donors, [{patients}] AS patients"


@dataclass(frozen=True)
class Arc:
    """A donor who can give to a patient outside the donor's own pair."""

    donor: str
    patient: str
    altruistic: bool


ARCS = """
MATCH (d:Donor)-[:COMPATIBLE]->(p:Patient)
RETURN d.id AS donor, p.id AS patient, d.altruistic AS altruistic
ORDER BY donor, patient
"""


PAIRED_PATIENTS = "MATCH (:Donor)-[:PAIRED_WITH]->(p:Patient) RETURN p.id AS id ORDER BY id"

# Patients per query. The engine builds a whole result as one value capped at 256 MB,
# and a dense pool has many cycles per patient (a 512-pair PrefLib instance exceeded
# the cap in one query), so cycles are fetched a batch of first patients at a time.
FIRST_PATIENT_BATCH = 32


def find_cycles(conn: relate.Connection, max_length: int = 3) -> list[Exchange]:
    patients = [row["id"] for row in conn.execute(PAIRED_PATIENTS)]
    batches = [patients[i : i + FIRST_PATIENT_BATCH] for i in range(0, len(patients), FIRST_PATIENT_BATCH)]
    return [
        Exchange("cycle", tuple(row["donors"]), tuple(row["patients"]))
        for length in range(2, max_length + 1)
        for batch in batches
        for row in conn.execute(_cycle_query(length), {"first": batch})
    ]


def find_arcs(conn: relate.Connection) -> list[Arc]:
    """Every compatible donor-to-patient arc.

    Chains are assembled by the solver from these arcs rather than enumerated here:
    with about 20k arcs, the number of chains grows with the arc count to the power
    of the chain length, and enumerating length-3 chains exceeds the engine's 256 MB
    value limit.
    """
    return [Arc(row["donor"], row["patient"], row["altruistic"]) for row in conn.execute(ARCS)]
