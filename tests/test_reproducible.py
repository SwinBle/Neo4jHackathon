import os
import subprocess
import sys

PROGRAM = """
import relate
from kidneyloop.generator import generate_pool
from kidneyloop.runs import read_inputs
from kidneyloop.solver import solve
from kidneyloop.store import derive, load_pool
conn = relate.connect(":memory:")
load_pool(conn, generate_pool(seed=42, pairs=120, altruists=5))
derive(conn)
inputs = read_inputs(conn)
for objective in ("count", "expected"):
    print(sorted((e.kind, e.donors, e.patients) for e in solve(inputs, 4, objective).exchanges))
"""


def plan_in_fresh_process(hash_seed: str) -> str:
    env = {**os.environ, "PYTHONHASHSEED": hash_seed}
    return subprocess.run([sys.executable, "-c", PROGRAM], env=env, capture_output=True, text=True, check=True).stdout


def test_same_pool_gives_the_same_plan_in_every_process():
    plans = {plan_in_fresh_process(seed) for seed in ("0", "1", "2", "3")}
    assert len(plans) == 1
