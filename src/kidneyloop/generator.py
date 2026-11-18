import random

from .model import (
    BLOOD_TYPES,
    HLA_A,
    HLA_B,
    HLA_DR,
    ANTIGENS,
    BloodType,
    Donor,
    Patient,
    Pool,
    can_donate,
)

# Illustrative frequencies for synthetic data, not population statistics.
BLOOD_WEIGHTS = (0.45, 0.40, 0.11, 0.04)
ANTIBODY_COUNT_BANDS = ((0.50, 0, 0), (0.25, 1, 3), (0.15, 4, 8), (0.10, 9, 16))


def _blood(rng: random.Random) -> BloodType:
    return rng.choices(BLOOD_TYPES, weights=BLOOD_WEIGHTS)[0]


def _antigens(rng: random.Random) -> frozenset[str]:
    return frozenset(rng.choices(HLA_A, k=2) + rng.choices(HLA_B, k=2) + rng.choices(HLA_DR, k=2))


def _antibodies(rng: random.Random) -> frozenset[str]:
    bands = ANTIBODY_COUNT_BANDS
    _, low, high = rng.choices(bands, weights=[b[0] for b in bands])[0]
    return frozenset(rng.sample(ANTIGENS, rng.randint(low, high)))


def generate_pool(seed: int = 42, pairs: int = 300, altruists: int = 10) -> Pool:
    """Incompatible patient-donor pairs plus altruistic donors, reproducible from the seed.

    A pair joins the pool only when its donor cannot give to its own patient,
    which is the reason real pairs enter paired exchange.
    """
    rng = random.Random(seed)
    patients: list[Patient] = []
    donors: list[Donor] = []
    while len(patients) < pairs:
        n = len(patients) + 1
        patient = Patient(f"P{n:03d}", _blood(rng), _antibodies(rng), rng.randint(30, 2500))
        donor = Donor(f"D{n:03d}", _blood(rng), _antigens(rng), patient.id)
        if can_donate(donor, patient):
            continue
        patients.append(patient)
        donors.append(donor)
    for n in range(1, altruists + 1):
        donors.append(Donor(f"N{n:02d}", _blood(rng), _antigens(rng), None))
    return Pool(tuple(patients), tuple(donors))
