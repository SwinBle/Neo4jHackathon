"""A matching policy: how patients are ranked when plans tie, and how likely a planned transplant is to go ahead.

A policy is a JSON file under policies/. It is parsed once, at the boundary, into the
frozen dataclasses below; a malformed file raises PolicyError naming the field.
"""

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from .model import BLOOD_TYPES, BloodType

# shortcut: assumes a source checkout (`pip install -e .`, the documented install); move
# policies/ into the package as data files if KidneyLoop is ever installed as a wheel.
DEFAULT_POLICY_FILE = Path(__file__).resolve().parents[2] / "policies" / "default.json"


class PolicyError(ValueError):
    pass


@dataclass(frozen=True)
class PatientFacts:
    """What the graph knows about a patient that a policy may score."""

    pool_pra: float
    wait_days: int
    blood: BloodType
    paired_donor_blood: BloodType | None


@dataclass(frozen=True)
class PoolFacts:
    max_wait: int


def pra_percent(pool_pra: float) -> int:
    # Bands are written in whole percent, as CPRA is reported. Rounding first stops
    # float noise (0.29 * 100 == 28.999...) from dropping a patient into the band below.
    return math.floor(round(pool_pra * 100, 9))


@dataclass(frozen=True)
class Bands:
    """A step function of pool PRA in whole percent: each band runs from its start to the next band's start."""

    starts: tuple[int, ...]
    values: tuple[float, ...]

    def at(self, pool_pra: float) -> float:
        percent = pra_percent(pool_pra)
        return next(v for s, v in zip(reversed(self.starts), reversed(self.values)) if percent >= s)


@dataclass(frozen=True)
class PoolPraLinear:
    per_unit: float

    def points(self, p: PatientFacts, pool: PoolFacts) -> float:
        return self.per_unit * p.pool_pra

    def bound(self, pool: PoolFacts) -> float:
        return self.per_unit * 1.0


@dataclass(frozen=True)
class WaitShareLinear:
    """Points for wait_days as a share of the longest wait in the pool."""

    per_unit: float

    def points(self, p: PatientFacts, pool: PoolFacts) -> float:
        return self.per_unit * (p.wait_days / pool.max_wait if pool.max_wait else 0.0)

    def bound(self, pool: PoolFacts) -> float:
        return self.per_unit * 1.0


@dataclass(frozen=True)
class WaitDays:
    per_day: float

    def points(self, p: PatientFacts, pool: PoolFacts) -> float:
        return self.per_day * p.wait_days

    def bound(self, pool: PoolFacts) -> float:
        return self.per_day * pool.max_wait


@dataclass(frozen=True)
class PoolPraBands:
    bands: Bands

    def points(self, p: PatientFacts, pool: PoolFacts) -> float:
        return self.bands.at(p.pool_pra)

    def bound(self, pool: PoolFacts) -> float:
        return max(self.bands.values)


@dataclass(frozen=True)
class PatientBlood:
    by_type: Mapping[str, float]

    def points(self, p: PatientFacts, pool: PoolFacts) -> float:
        return self.by_type[p.blood]

    def bound(self, pool: PoolFacts) -> float:
        return max(self.by_type.values())


@dataclass(frozen=True)
class PairedDonorBlood:
    by_type: Mapping[str, float]

    def points(self, p: PatientFacts, pool: PoolFacts) -> float:
        return self.by_type[p.paired_donor_blood] if p.paired_donor_blood else 0.0

    def bound(self, pool: PoolFacts) -> float:
        return max(self.by_type.values())


Term = PoolPraLinear | WaitShareLinear | WaitDays | PoolPraBands | PatientBlood | PairedDonorBlood


@dataclass(frozen=True)
class SuccessModel:
    """The chance that one planned transplant goes ahead, from the recipient's pool PRA.

    success = 1 - crossmatch failure for the patient's band - exogenous failure.
    """

    crossmatch_failure: Bands
    exogenous_failure: float

    def probability(self, pool_pra: float) -> float:
        return 1.0 - self.crossmatch_failure.at(pool_pra) - self.exogenous_failure


@dataclass(frozen=True)
class Policy:
    name: str
    sha256: str
    tie_break: tuple[Term, ...]
    success: SuccessModel

    def tie(self, p: PatientFacts, pool: PoolFacts) -> float:
        """The patient's tie-break score scaled to [0, 1] by the policy's largest possible score in this pool."""
        bound = sum(t.bound(pool) for t in self.tie_break)
        if bound == 0:
            return 0.0
        return sum((t.points(p, pool) for t in self.tie_break), 0.0) / bound


def _field(obj: Mapping[str, Any], key: str, where: str, kind: type | tuple[type, ...]) -> Any:
    if key not in obj:
        raise PolicyError(f"{where}.{key} is missing")
    value = obj[key]
    if isinstance(value, bool) or not isinstance(value, kind):
        raise PolicyError(f"{where}.{key} must be {kind.__name__ if isinstance(kind, type) else 'a number'}")
    return value


def _number(obj: Mapping[str, Any], key: str, where: str, low: float = 0.0, high: float = math.inf) -> float:
    value = float(_field(obj, key, where, (int, float)))
    if not low <= value <= high:
        raise PolicyError(f"{where}.{key} must be between {low} and {high}, got {value}")
    return value


def _bands(obj: Mapping[str, Any], key: str, where: str, high: float) -> Bands:
    rows = _field(obj, key, where, list)
    starts: list[int] = []
    values: list[float] = []
    for i, row in enumerate(rows):
        at = f"{where}.{key}[{i}]"
        if not isinstance(row, dict):
            raise PolicyError(f"{at} must be an object with from_percent and a value")
        start = _field(row, "from_percent", at, int)
        if starts and start <= starts[-1] or not starts and start != 0 or start > 100:
            raise PolicyError(f"{at}.from_percent must start at 0, rise, and stay at most 100")
        starts.append(start)
        values.append(_number(row, "value", at, 0.0, high))
    if not starts:
        raise PolicyError(f"{where}.{key} needs at least one band")
    return Bands(tuple(starts), tuple(values))


def _blood_points(obj: Mapping[str, Any], where: str) -> dict[str, float]:
    points = _field(obj, "points", where, dict)
    if set(points) != set(BLOOD_TYPES):
        raise PolicyError(f"{where}.points must name exactly the blood types {', '.join(BLOOD_TYPES)}")
    return {b: _number(points, b, f"{where}.points") for b in BLOOD_TYPES}


TERMS: dict[str, Callable[[Mapping[str, Any], str], Term]] = {
    "pool_pra_linear": lambda o, w: PoolPraLinear(_number(o, "per_unit", w)),
    "wait_share_linear": lambda o, w: WaitShareLinear(_number(o, "per_unit", w)),
    "wait_days": lambda o, w: WaitDays(_number(o, "per_day", w)),
    "pool_pra_bands": lambda o, w: PoolPraBands(_bands(o, "bands", w, math.inf)),
    "patient_blood": lambda o, w: PatientBlood(_blood_points(o, w)),
    "paired_donor_blood": lambda o, w: PairedDonorBlood(_blood_points(o, w)),
}


def parse_policy(data: Any, sha256: str) -> Policy:
    if not isinstance(data, dict):
        raise PolicyError("policy must be a JSON object")
    name = _field(data, "name", "policy", str)
    terms = []
    for i, term in enumerate(_field(data, "tie_break", "policy", list)):
        where = f"tie_break[{i}]"
        if not isinstance(term, dict):
            raise PolicyError(f"{where} must be an object")
        kind = _field(term, "kind", where, str)
        if kind not in TERMS:
            raise PolicyError(f"{where}.kind must be one of {', '.join(TERMS)}, got {kind!r}")
        terms.append(TERMS[kind](term, where))
    success = _field(data, "success", "policy", dict)
    model = SuccessModel(
        _bands(success, "crossmatch_failure", "success", 1.0),
        _number(success, "exogenous_failure", "success", 0.0, 1.0),
    )
    if any(v + model.exogenous_failure > 1.0 for v in model.crossmatch_failure.values):
        raise PolicyError("success: crossmatch_failure plus exogenous_failure must stay at most 1")
    return Policy(name, sha256, tuple(terms), model)


def load_policy(path: Path = DEFAULT_POLICY_FILE) -> Policy:
    raw = path.read_bytes()
    # Hash with LF line endings so a Windows checkout (core.autocrlf) and a Unix one
    # record the same hash for the same policy.
    digest = hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise PolicyError(f"{path} is not valid JSON: {e}")
    return parse_policy(data, digest)
