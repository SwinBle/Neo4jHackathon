"""Measure, on sampled transplant outcomes, how many patients each objective's plan really serves."""

import math
from dataclasses import dataclass

import numpy as np
import relate

from .policy import Policy, pra_percent
from .runs import Progress, _quiet, read_inputs, solve_with_progress
from .solver import OBJECTIVES, Objective, Plan, expected_transplants

POOL_PRA = "MATCH (p:Patient) RETURN p.id AS id, p.pool_pra AS pool_pra ORDER BY id"


@dataclass(frozen=True)
class Estimate:
    mean: float
    low: float
    high: float

    @staticmethod
    def of(samples: np.ndarray) -> "Estimate":
        """Mean with a normal-approximation 95% interval."""
        mean = float(samples.mean())
        half = 1.96 * float(samples.std(ddof=1)) / math.sqrt(len(samples)) if len(samples) > 1 else 0.0
        return Estimate(mean, mean - half, mean + half)


@dataclass(frozen=True)
class Arm:
    objective: Objective
    plan: Plan
    expected: float
    realized: np.ndarray
    realized_by_band: tuple[np.ndarray, ...]


@dataclass(frozen=True)
class Simulation:
    policy: str
    trials: int
    seed: int
    band_starts: tuple[int, ...]
    band_patients: tuple[int, ...]
    arms: tuple[Arm, ...]

    def arm(self, objective: Objective) -> Arm:
        return next(a for a in self.arms if a.objective == objective)

    @property
    def gain(self) -> Estimate:
        """Realized transplants of the expected plan minus the count plan, paired by trial."""
        return Estimate.of(self.arm("expected").realized - self.arm("count").realized)


def simulate(
    conn: relate.Connection,
    policy: Policy,
    trials: int,
    seed: int,
    max_cycle: int = 3,
    max_chain: int = 4,
    progress: Progress = _quiet,
) -> Simulation:
    """Solve the pool once per objective, then replay both plans on the same sampled outcomes.

    Each planned donor-to-patient transplant succeeds with the policy's success
    probability for that patient, independently. A transplant both plans use gets one
    draw per trial, shared by the two plans. A cycle happens only if all its
    transplants succeed; a chain stops at its first failure.
    """
    inputs = read_inputs(conn, max_cycle, progress, policy)
    plans = {o: solve_with_progress(inputs, max_chain, progress, o) for o in OBJECTIVES}
    arcs = sorted({arc for plan in plans.values() for e in plan.exchanges for arc in zip(e.donors, e.patients)})
    column = {arc: i for i, arc in enumerate(arcs)}
    chance = np.array([inputs.success[p] for _, p in arcs])
    progress(f"Sampling {trials} outcomes of {len(arcs)} planned transplants (seed {seed})...")
    works = np.random.default_rng(seed).random((trials, len(arcs))) < chance

    starts = policy.success.crossmatch_failure.starts
    band_of = {
        row["id"]: max(i for i, s in enumerate(starts) if pra_percent(row["pool_pra"]) >= s)
        for row in conn.execute(POOL_PRA)
    }
    arms = []
    for objective, plan in plans.items():
        by_band = [np.zeros(trials, dtype=int) for _ in starts]
        for e in plan.exchanges:
            steps = works[:, [column[arc] for arc in zip(e.donors, e.patients)]]
            received = np.repeat(steps.all(axis=1, keepdims=True), e.size, axis=1) if e.kind == "cycle" else np.cumprod(steps, axis=1)
            for i, p in enumerate(e.patients):
                by_band[band_of[p]] += received[:, i]
        arms.append(Arm(objective, plan, expected_transplants(plan, inputs.success), sum(by_band), tuple(by_band)))
    patients = tuple(sum(b == i for b in band_of.values()) for i in range(len(starts)))
    return Simulation(policy.name, trials, seed, starts, patients, tuple(arms))


def band_label(starts: tuple[int, ...], i: int) -> str:
    end = starts[i + 1] - 1 if i + 1 < len(starts) else 100
    return f"{starts[i]}-{end}%"


def render(sim: Simulation) -> list[str]:
    def fmt(e: Estimate) -> str:
        return f"{e.mean:.1f} [{e.low:.1f}, {e.high:.1f}]"

    lines = [
        f"{sim.trials} trials, seed {sim.seed}, policy {sim.policy}; same sampled outcomes for both plans",
        f"{'plan':<15}{'planned':>8}{'expected':>10}  realized mean [95% CI]",
    ]
    for arm in sim.arms:
        lines.append(f"{arm.objective:<15}{arm.plan.transplants:>8}{arm.expected:>10.1f}  {fmt(Estimate.of(arm.realized))}")
    lines.append(f"expected - count, paired by trial: {sim.gain.mean:+.1f} [{sim.gain.low:+.1f}, {sim.gain.high:+.1f}]")
    lines.append("Realized transplants by pool PRA band, mean per trial:")
    for i, n in enumerate(sim.band_patients):
        cells = ", ".join(f"{a.objective} {a.realized_by_band[i].mean():.1f}" for a in sim.arms)
        lines.append(f"  {band_label(sim.band_starts, i):<8} ({n} patients): {cells}")
    return lines
