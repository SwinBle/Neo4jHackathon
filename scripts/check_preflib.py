"""Reproduce the README's PrefLib figures: cycle counts checked against a separate search.

Run from the repo root:
    python scripts/check_preflib.py 111          # cycle counts only
    python scripts/check_preflib.py 111 --match  # also solve and print transplants

Each instance is downloaded once from a pinned PrefLib-Data commit into
data/cache/preflib/ (not committed), imported into a temporary store, and its cycles
of up to 3 are counted twice: by KidneyLoop's Cypher and by a plain Python search over
the dataset's arcs that shares no code with it. Exits 1 if the counts differ.
"""

import argparse
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import relate

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from kidneyloop.datasets import read_preflib  # noqa: E402
from kidneyloop.exchanges import find_cycles  # noqa: E402
from kidneyloop.runs import run_match  # noqa: E402
from kidneyloop.store import create_json_store, load_imported  # noqa: E402

# The latest commit touching dataset 00036 when the README figures were measured.
PREFLIB_COMMIT = "0ee5e34c15ed20d93d870f6b3905e0f664bdb8c0"
PREFLIB_URL = f"https://raw.githubusercontent.com/PrefLib/PrefLib-Data/{PREFLIB_COMMIT}/datasets/00036%20-%20kidney/"


def download(instance: int, cache: Path) -> Path:
    dat = cache / f"00036-{instance:08d}.dat"
    for path in (dat, dat.with_suffix(".wmd")):
        if not path.exists():
            cache.mkdir(parents=True, exist_ok=True)
            with urllib.request.urlopen(PREFLIB_URL + path.name, timeout=120) as response:
                path.write_bytes(response.read())
    return dat


def count_cycles(arcs: list[tuple[int, int]]) -> tuple[int, int]:
    """2- and 3-cycles among pairs from arcs i -> j (pair i's donor gives to pair j's patient).

    Each cycle is counted from its smallest pair a, so its rotations count once; the
    two directions around one triple are different exchanges and count twice.
    """
    succ: dict[int, set[int]] = {}
    for i, j in arcs:
        succ.setdefault(i, set()).add(j)
    two = three = 0
    for a, out in succ.items():
        for b in out:
            if b <= a:
                continue
            if a in succ.get(b, ()):
                two += 1
            three += sum(1 for c in succ.get(b, ()) if c > a and a in succ.get(c, ()))
    return two, three


def wmd_arcs(wmd: Path) -> list[tuple[int, int]]:
    arcs = []
    for line in wmd.read_text().splitlines():
        if line.strip() and not line.startswith("#"):
            i, j, _ = line.split(",")
            arcs.append((int(i), int(j)))
    return arcs


def check(instance: int, cache: Path, match: bool) -> bool:
    dat = download(instance, cache)
    imported = read_preflib(dat, dat.with_suffix(".wmd"))
    two, three = count_cycles(wmd_arcs(dat.with_suffix(".wmd")))
    with tempfile.TemporaryDirectory() as tmp:
        store = Path(tmp) / "store"
        create_json_store(store)
        with relate.connect(str(store), read_only=False) as conn:
            load_imported(conn, imported)
            start = time.perf_counter()
            cycles = find_cycles(conn, 3)
            took = time.perf_counter() - start
            kx_two = sum(1 for c in cycles if c.size == 2)
            kx_three = len(cycles) - kx_two
            same = (kx_two, kx_three) == (two, three)
            print(
                f"instance {instance}: {len(imported.pool.patients)} pairs, {len(imported.arcs)} arcs; "
                f"Cypher {kx_two} + {kx_three} = {len(cycles)} cycles in {took:.1f} s; "
                f"separate search {two} + {three} = {two + three}; {'same' if same else 'DIFFERENT'}"
            )
            if match and same:
                start = time.perf_counter()
                summary = run_match(conn)
                print(f"instance {instance}: kx match plans {summary.plan.transplants} transplants in {time.perf_counter() - start:.1f} s")
    return same


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("instances", nargs="+", type=int, help="PrefLib 00036 instance numbers, such as 16 111 200")
    parser.add_argument("--match", action="store_true", help="also run kx match and print the transplant count")
    parser.add_argument("--cache", type=Path, default=REPO / "data" / "cache" / "preflib")
    args = parser.parse_args()
    results = [check(n, args.cache, args.match) for n in args.instances]
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
