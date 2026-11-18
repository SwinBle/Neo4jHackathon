from dataclasses import dataclass
from typing import Literal

BloodType = Literal["O", "A", "B", "AB"]
BLOOD_TYPES: tuple[BloodType, ...] = ("O", "A", "B", "AB")

ABO_CAN_GIVE_TO: dict[BloodType, tuple[BloodType, ...]] = {
    "O": ("O", "A", "B", "AB"),
    "A": ("A", "AB"),
    "B": ("B", "AB"),
    "AB": ("AB",),
}

HLA_A = ("A1", "A2", "A3", "A11", "A24", "A26", "A29", "A30", "A31", "A33")
HLA_B = ("B7", "B8", "B13", "B35", "B38", "B44", "B46", "B51", "B52", "B58", "B60", "B62")
HLA_DR = ("DR1", "DR4", "DR7", "DR9", "DR11", "DR12", "DR13", "DR14", "DR15", "DR17")
ANTIGENS = HLA_A + HLA_B + HLA_DR


@dataclass(frozen=True)
class Donor:
    id: str
    blood_type: BloodType
    antigens: frozenset[str]
    paired_with: str | None


@dataclass(frozen=True)
class Patient:
    id: str
    blood_type: BloodType
    antibodies: frozenset[str]
    wait_days: int


@dataclass(frozen=True)
class Pool:
    patients: tuple[Patient, ...]
    donors: tuple[Donor, ...]


def can_donate(donor: Donor, patient: Patient) -> bool:
    return patient.blood_type in ABO_CAN_GIVE_TO[donor.blood_type] and not (
        donor.antigens & patient.antibodies
    )
