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
    hops = "".join(
        f"-[:COMPATIBLE]->(p{i}:Patient)<-[:PAIRED_WITH]-(d{i}:Donor)" for i in range(2, length + 1)
    )
    distinct = " AND ".join(f"p1.id < p{i}.id" for i in range(2, length + 1))
    if length == 3:
        distinct += " AND p2 <> p3"
    donors = ", ".join(f"d{i}.id" for i in [length, *range(1, length)])
    patients = ", ".join(f"p{i}.id" for i in range(1, length + 1))
    return (
        f"MATCH (p1:Patient)<-[:PAIRED_WITH]-(d1:Donor){hops}-[:COMPATIBLE]->(p1) "
        f"WHERE {distinct} RETURN [{donors}] AS donors, [{patients}] AS patients"
    )


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


def find_cycles(conn: relate.Connection, max_length: int = 3) -> list[Exchange]:
    return [
        Exchange("cycle", tuple(row["donors"]), tuple(row["patients"]))
        for length in range(2, max_length + 1)
        for row in conn.execute(_cycle_query(length))
    ]


def find_arcs(conn: relate.Connection) -> list[Arc]:
    """Every compatible donor-to-patient arc.

    Chains are assembled by the solver from these arcs rather than enumerated here:
    with about 20k arcs, the number of chains grows with the arc count to the power
    of the chain length, and enumerating length-3 chains exceeds the engine's 256 MB
    value limit.
    """
    return [Arc(row["donor"], row["patient"], row["altruistic"]) for row in conn.execute(ARCS)]
