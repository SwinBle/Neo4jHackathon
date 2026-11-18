import itertools
import json
import random
from pathlib import Path

import pytest

from kidneyloop.model import BLOOD_TYPES
from kidneyloop.policy import DEFAULT_POLICY_FILE, PatientFacts, PolicyError, PoolFacts, load_policy, parse_policy
from kidneyloop.solver import patient_weight

POLICIES = sorted((Path(__file__).resolve().parents[1] / "policies").glob("*.json"))
OPTN = DEFAULT_POLICY_FILE.parent / "optn-kpd-2019-inspired.json"


def test_default_policy_reproduces_the_original_weights_exactly():
    policy = load_policy()
    rng = random.Random(0)
    for _ in range(2000):
        pra, wait, max_wait, n = rng.random(), rng.randint(0, 2500), rng.choice([0, 2500]), rng.randint(1, 400)
        original = 1.0 + (0.7 * pra + 0.3 * (wait / max_wait if max_wait else 0.0)) / (n + 1)
        facts = PatientFacts(pra, wait, rng.choice(BLOOD_TYPES), rng.choice([*BLOOD_TYPES, None]))
        assert patient_weight(policy.tie(facts, PoolFacts(max_wait)), n) == original


@pytest.mark.parametrize("path", POLICIES, ids=lambda p: p.stem)
def test_every_policy_keeps_the_tie_break_below_one_transplant(path):
    policy = load_policy(path)
    pool = PoolFacts(max_wait=2500)
    n = 300
    facts = [
        PatientFacts(pra, wait, blood, donor)
        for pra, wait, blood, donor in itertools.product(
            [0.0, 0.19, 0.5, 0.8, 0.95, 0.99, 1.0], [0, 1, 2500], BLOOD_TYPES, [*BLOOD_TYPES, None]
        )
    ]
    ties = [policy.tie(f, pool) for f in facts]
    assert all(0.0 <= t <= 1.0 for t in ties)
    assert n * (patient_weight(max(ties), n) - 1.0) < 1.0


@pytest.mark.parametrize("path", POLICIES, ids=lambda p: p.stem)
def test_every_policy_gives_success_chances_between_zero_and_one(path):
    model = load_policy(path).success
    assert all(0.0 <= model.probability(pra / 100) <= 1.0 for pra in range(101))


def test_success_model_follows_the_cited_crossmatch_bands():
    model = load_policy().success
    assert [round(model.probability(pra), 9) for pra in (0.10, 0.25, 0.74, 0.75, 1.0)] == [0.87, 0.72, 0.57, 0.42, 0.42]


def test_optn_inspired_policy_scores_the_rows_of_table_13_2():
    policy = load_policy(OPTN)
    pool = PoolFacts(max_wait=1000)
    bound = 0.07 * 1000 + 2000 + 125 + 100 + 500
    sensitized = PatientFacts(0.85, 1000, "O", "AB")
    assert policy.tie(sensitized, pool) == pytest.approx((70 + 200 + 125 + 100 + 500) / bound)
    # 0.29 * 100 is 28.999... in floating point; it must still land in the 20-29 band.
    assert policy.tie(PatientFacts(0.29, 0, "AB", "O"), pool) == pytest.approx(5 / bound)
    assert policy.tie(PatientFacts(0.30, 0, "AB", "O"), pool) == pytest.approx(10 / bound)


def test_policy_hash_ignores_line_endings(tmp_path):
    text = DEFAULT_POLICY_FILE.read_bytes().replace(b"\r\n", b"\n")
    (tmp_path / "lf.json").write_bytes(text)
    (tmp_path / "crlf.json").write_bytes(text.replace(b"\n", b"\r\n"))
    assert load_policy(tmp_path / "lf.json").sha256 == load_policy(tmp_path / "crlf.json").sha256


def valid() -> dict:
    return json.loads(DEFAULT_POLICY_FILE.read_text())


@pytest.mark.parametrize(
    "change, field",
    [
        (lambda d: d.pop("tie_break"), "policy.tie_break"),
        (lambda d: d["tie_break"].append({"kind": "lottery"}), "tie_break[2].kind"),
        (lambda d: d["tie_break"].__setitem__(0, {"kind": "pool_pra_linear", "per_unit": -1}), "tie_break[0].per_unit"),
        (lambda d: d["tie_break"].append({"kind": "patient_blood", "points": {"O": 1}}), "tie_break[2].points"),
        (lambda d: d["success"]["crossmatch_failure"][0].__setitem__("from_percent", 5), "success.crossmatch_failure[0].from_percent"),
        (lambda d: d["success"]["crossmatch_failure"][1].__setitem__("value", 1.5), "success.crossmatch_failure[1].value"),
        (lambda d: d["success"].__setitem__("exogenous_failure", 0.9), "success"),
        (lambda d: d.__setitem__("name", 3), "policy.name"),
    ],
)
def test_bad_policy_names_the_field(change, field):
    data = valid()
    change(data)
    with pytest.raises(PolicyError, match=field.replace("[", r"\[").replace("]", r"\]")):
        parse_policy(data, "hash")
