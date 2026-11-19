"""Plant known bugs one at a time and confirm the test suite fails on each.

Run from the repo root: python scripts/mutation_check.py
Each mutation is applied to the source file, the named tests run, and the file is
restored whatever happens.
"""

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

MUTATIONS = [
    (
        "patient may receive twice",
        "src/kidneyloop/solver.py",
        "        add_row([(v, 1.0) for v in vars_], 1.0)\n",
        "        pass\n",
        ["tests/test_solver.py"],
    ),
    (
        "chain donor gives without own patient receiving",
        "src/kidneyloop/solver.py",
        "                add_row([(v, 1.0) for v in out] + [(v, -1.0) for v in receives_at.get((patient, k), [])], 0.0)\n",
        "                pass\n",
        ["tests/test_solver.py"],
    ),
    (
        "cycles reported once per rotation",
        "src/kidneyloop/exchanges.py",
        ' WHERE p1.id < p2.id"\n    ]',
        '"\n    ]',
        ["tests/test_exchanges.py", "tests/test_solver.py"],
    ),
    (
        "antibodies ignored",
        "src/kidneyloop/store.py",
        "  AND NOT EXISTS { (p)-[:HAS_ANTIBODY]->(:Antigen)<-[:HAS_ANTIGEN]-(d) }\n",
        "",
        ["tests/test_compatibility.py"],
    ),
    (
        "what-if commits instead of rolling back",
        "src/kidneyloop/whatif.py",
        "        conn.rollback()\n",
        "        conn.commit()\n",
        ["tests/test_runs.py"],
    ),
    (
        "plan depends on the order rows arrive in",
        "src/kidneyloop/solver.py",
        "    cycles = sorted(inputs.cycles, key=lambda c: (c.patients, c.donors))\n    arcs = sorted(inputs.arcs, key=lambda a: (a.donor, a.patient))\n",
        "    cycles = list(inputs.cycles)\n    arcs = list(inputs.arcs)\n",
        ["tests/test_solver.py"],
    ),
    (
        "plan depends on Python's hash seed",
        "src/kidneyloop/solver.py",
        "    for donor in sorted({a.donor for a in first}):\n",
        "    for donor in {a.donor for a in first}:\n",
        ["tests/test_reproducible.py"],
    ),
    (
        "tie-break can outweigh a transplant",
        "src/kidneyloop/solver.py",
        "return 1.0 + tie / (patients + 1)",
        "return 1.0 + tie",
        ["tests/test_solver.py"],
    ),
    (
        "expected objective ignores chain position",
        "src/kidneyloop/solver.py",
        "        return q[arc.patient] * q[patient_of[arc.donor]] * q_mean ** (k - 2)\n",
        "        return q[arc.patient]\n",
        ["tests/test_solver.py"],
    ),
    (
        "expected objective treats a cycle like independent transplants",
        "src/kidneyloop/solver.py",
        "        return math.prod(q[p] for p in cycle.patients) if objective == \"expected\" else 1.0\n",
        "        return 1.0\n",
        ["tests/test_solver.py"],
    ),
    (
        "simulated chain continues past a failed transplant",
        "src/kidneyloop/simulate.py",
        "np.cumprod(steps, axis=1)",
        "steps",
        ["tests/test_simulate.py"],
    ),
    (
        "input hash ignores compatible edges",
        "src/kidneyloop/runs.py",
        '        "compatible": sorted([r["donor"], r["patient"]] for r in conn.execute(HASH_ARCS)),\n',
        "",
        ["tests/test_audit.py"],
    ),
    (
        "counterfactual commits instead of rolling back",
        "src/kidneyloop/counterfactual.py",
        "            conn.rollback()\n",
        "            conn.commit()\n",
        ["tests/test_counterfactual.py"],
    ),
    (
        "counterfactual ignores the run's cycle limit",
        "src/kidneyloop/counterfactual.py",
        "            inputs = read_inputs(conn, max_cycle, progress, policy, run[\"seq\"])\n",
        "            inputs = read_inputs(conn, 3, progress, policy, run[\"seq\"])\n",
        ["tests/test_counterfactual.py"],
    ),
    (
        "cycle batches skip patients",
        "src/kidneyloop/exchanges.py",
        "range(0, len(patients), FIRST_PATIENT_BATCH)",
        "range(0, len(patients), 2 * FIRST_PATIENT_BATCH)",
        ["tests/test_exchanges.py"],
    ),
    (
        "imported arcs skip the blood-type check",
        "src/kidneyloop/datasets.py",
        "        if patient.blood_type not in ABO_CAN_GIVE_TO[donor.blood_type]:\n",
        "        if False:\n",
        ["tests/test_datasets.py"],
    ),
    (
        "CPRA forgets that a donor has two haplotypes",
        "src/kidneyloop/cpra.py",
        "    return max(0.0, 1.0 - clear * clear)\n",
        "    return max(0.0, 1.0 - clear)\n",
        ["tests/test_cpra.py"],
    ),
    (
        "held exchanges are planned again",
        "src/kidneyloop/runs.py",
        "        inputs = without(inputs, taken_patients, taken_donors)\n",
        "        pass\n",
        ["tests/test_lifecycle.py"],
    ),
    (
        "a run's own statuses make it look stale",
        "src/kidneyloop/runs.py",
        " AND ($before IS NULL OR r.seq < $before)",
        "",
        ["tests/test_lifecycle.py"],
    ),
    (
        "illegal status moves are accepted",
        "src/kidneyloop/lifecycle.py",
        "    if status not in TRANSITIONS[previous]:\n",
        "    if False:\n",
        ["tests/test_lifecycle.py"],
    ),
    (
        "a superseded proposal is accepted",
        "src/kidneyloop/lifecycle.py",
        " and rows[0][\"seq\"] != rows[0][\"last\"]:",
        ":",
        ["tests/test_lifecycle.py"],
    ),
]


def main() -> int:
    survived = []
    for name, rel, old, new, tests in MUTATIONS:
        path = REPO / rel
        original = path.read_bytes()
        text = original.decode().replace("\r\n", "\n")
        if old not in text:
            print(f"SETUP ERROR {name}: anchor text not found in {rel}")
            return 2
        try:
            path.write_bytes(text.replace(old, new, 1).encode())
            result = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", *tests],
                cwd=REPO, capture_output=True, text=True, timeout=600,
            )
        finally:
            path.write_bytes(original)
        caught = result.returncode != 0
        print(f"{'caught  ' if caught else 'SURVIVED'} {name}")
        if not caught:
            survived.append(name)
    return 1 if survived else 0


if __name__ == "__main__":
    sys.exit(main())
