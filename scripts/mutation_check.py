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
        'WHERE p1.id < p2.id"]',
        '"]',
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
        "tie-break can outweigh a transplant",
        "src/kidneyloop/solver.py",
        "return 1.0 + tie / (patients + 1)",
        "return 1.0 + tie",
        ["tests/test_solver.py"],
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
