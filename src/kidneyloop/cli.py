from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import relate
import typer

from .counterfactual import counterfactual as find_counterfactual, render as render_counterfactual
from .exchanges import find_arcs, find_cycles
from .cpra import DEFAULT_POPULATION, NMDP_RECORD, SEROLOGY_RELEASE, CpraDataError, load_reference
from .datasets import DatasetError, read_preflib
from .explain import explain, render
from .lifecycle import STATUSES, TransitionError, set_status, statuses
from .generator import generate_pool
from .policy import DEFAULT_POLICY_FILE, Policy, PolicyError, load_policy
from .runs import check_inputs, latest_run_id, run_match, run_record
from .simulate import render as render_simulation, simulate as simulate_plans
from .solver import OBJECTIVES, Objective
from .store import create_json_store, derive, load_imported, load_pool, set_cpra
from .whatif import withdraw_donor

DEFAULT_STORE = Path("data/kidneyloop")

app = typer.Typer(no_args_is_help=True)


@app.callback()
def main() -> None:
    """KidneyLoop: kidney paired-donation matching on a Relate graph."""


def fail(message: str, code: int) -> typer.Exit:
    typer.echo(message, err=True)
    return typer.Exit(code)


@contextmanager
def open_store(store: Path, read_only: bool) -> Iterator[relate.Connection]:
    if not (store / "manifest.json").exists():
        raise fail(f"No store at {store}. Run `kx build` first.", 2)
    try:
        conn = relate.connect(str(store), read_only=read_only)
    except relate.OperationalError as e:
        if "locked" in str(e):
            raise fail(f"{store} is open for writing by another process; wait for it to finish.", 5)
        raise
    with conn:
        yield conn


def progress(line: str) -> None:
    typer.echo(f"  - {line}", err=True)


def check_cycle_limit(max_cycle: int) -> None:
    if not 2 <= max_cycle <= 3:
        raise fail("--max-cycle must be 2 or 3.", 2)


def read_policy(path: Path) -> Policy:
    try:
        return load_policy(path)
    except OSError as e:
        raise fail(f"cannot read policy {path}: {e.strerror}", 2)
    except PolicyError as e:
        raise fail(f"bad policy {path}: {e}", 2)


@app.command()
def version() -> None:
    """Print the KidneyLoop and Relate engine versions."""
    typer.echo(f"kidneyloop 0.1.0, relate {relate.__version__}")


@app.command()
def build(
    store: Path = DEFAULT_STORE,
    seed: int = 42,
    pairs: int = 300,
    altruists: int = 10,
) -> None:
    """Generate a synthetic pool and write it, with derived compatibility, to a new store."""
    if pairs < 1 or altruists < 0:
        raise fail("--pairs must be at least 1 and --altruists at least 0.", 2)
    try:
        create_json_store(store)
    except (FileExistsError, RuntimeError) as e:
        raise fail(str(e), 2)
    with open_store(store, read_only=False) as conn:
        load_pool(conn, generate_pool(seed, pairs, altruists))
        derive(conn)
        counts = conn.execute(
            "MATCH (p:Patient) WITH count(p) AS patients "
            "MATCH (d:Donor) WITH patients, count(d) AS donors "
            "OPTIONAL MATCH ()-[c:COMPATIBLE]->() RETURN patients, donors, count(c) AS arcs"
        ).single()
    typer.echo(f"{store}: {counts['patients']} patients, {counts['donors']} donors, {counts['arcs']} compatible arcs")


@app.command("import-preflib")
def import_preflib(
    dat: Path = typer.Argument(..., help="The instance's .dat file (pairs, blood types, PRA)"),
    wmd: Path = typer.Argument(None, help="The instance's .wmd arc list; defaults to the .dat path with .wmd"),
    store: Path = DEFAULT_STORE,
) -> None:
    """Import a PrefLib kidney instance (dataset 00036) into a new store, keeping its arcs."""
    wmd = wmd or dat.with_suffix(".wmd")
    try:
        imported = read_preflib(dat, wmd)
    except OSError as e:
        raise fail(f"Cannot read {e.filename}: {e.strerror}", 2)
    except DatasetError as e:
        raise fail(str(e), 2)
    try:
        create_json_store(store)
    except (FileExistsError, RuntimeError) as e:
        raise fail(str(e), 2)
    with open_store(store, read_only=False) as conn:
        load_imported(conn, imported)
    typer.echo(f"{store}: {imported.source}, {len(imported.pool.patients)} pairs, {len(imported.arcs)} compatible arcs from the dataset")


@app.command()
def cpra(
    population: str = typer.Option(DEFAULT_POPULATION, help="NMDP population code; SCSEAI is Other Southeast Asian"),
    cache: Path = typer.Option(Path("data/cache"), help="Where the downloaded reference files are kept"),
    store: Path = DEFAULT_STORE,
) -> None:
    """Compute each patient's CPRA against NMDP haplotype frequencies and store it as p.cpra."""
    try:
        table = load_reference(cache, population)
    except (CpraDataError, OSError) as e:
        raise fail(f"Cannot load the CPRA reference: {e}", 2)
    with open_store(store, read_only=False) as conn:
        try:
            values = set_cpra(conn, table, NMDP_RECORD, SEROLOGY_RELEASE)
        except ValueError as e:
            raise fail(str(e), 2)
        rows = conn.execute("MATCH (p:Patient) RETURN p.id AS id, p.pool_pra AS pool_pra ORDER BY p.pool_pra DESC, id LIMIT 5")
        top = [(r["id"], r["pool_pra"]) for r in rows]
    typer.echo(f"CPRA for {len(values)} patients against {NMDP_RECORD}, population {population} ({SEROLOGY_RELEASE}).")
    typer.echo(f"Haplotypes with an allele that has no single antigen: {table.unmapped:.2%} of the frequency, counted as safe.")
    for patient, pool_pra in top:
        typer.echo(f"  {patient}: pool PRA {pool_pra:.0%}, CPRA {values[patient]:.0%}")
    typer.echo("Matching still reads pool PRA unless the policy sets \"sensitisation\": \"cpra\".")


@app.command()
def options(store: Path = DEFAULT_STORE, max_cycle: int = 3) -> None:
    """Count the cycles and arcs a matching round can choose from."""
    check_cycle_limit(max_cycle)
    with open_store(store, read_only=True) as conn:
        cycles = find_cycles(conn, max_cycle)
        arcs = find_arcs(conn)
    for size in range(2, max_cycle + 1):
        typer.echo(f"{size}-way cycles: {sum(c.size == size for c in cycles)}")
    typer.echo(f"altruistic arcs: {sum(a.altruistic for a in arcs)}, pair arcs: {sum(not a.altruistic for a in arcs)}")


def warn_if_stale(conn: relate.Connection) -> None:
    run = latest_run_id(conn)
    warning = check_inputs(conn, run).warning() if run else None
    if warning:
        typer.echo(warning)


def check_objective(objective: str) -> Objective:
    if objective not in OBJECTIVES:
        raise fail(f"--objective must be one of {', '.join(OBJECTIVES)}.", 2)
    return objective  # type: ignore[return-value]


@app.command()
def match(
    store: Path = DEFAULT_STORE,
    max_cycle: int = 3,
    max_chain: int = 4,
    policy: Path = typer.Option(DEFAULT_POLICY_FILE, help="Policy JSON file"),
    objective: str = typer.Option("count", help="count: max transplants; expected: max expected transplants"),
) -> None:
    """Choose disjoint cycles and chains and record them as a new MatchRun."""
    check_cycle_limit(max_cycle)
    if max_chain < 0:
        raise fail("--max-chain must be at least 0.", 2)
    goal = check_objective(objective)
    rules = read_policy(policy)
    with open_store(store, read_only=False) as conn:
        try:
            run = run_match(conn, max_cycle, max_chain, progress, rules, goal)
        except LookupError as e:
            raise fail(str(e), 2)
        patients = conn.execute("MATCH (p:Patient) RETURN count(p) AS n").single()["n"]
    cycles = [e for e in run.plan.exchanges if e.kind == "cycle"]
    chains = [e for e in run.plan.exchanges if e.kind == "chain"]
    typer.echo(f"{run.id}: {run.plan.transplants} transplants for {patients} patients ({goal} objective, policy {rules.name})")
    typer.echo(f"  from {run.candidate_cycles} candidate cycles and {run.candidate_arcs} arcs")
    typer.echo(f"  {len(cycles)} cycles, {len(chains)} chains, {patients - len(run.plan.matched_patients)} patients unmatched")
    typer.echo(f"  {run.expected_transplants:.1f} expected transplants under the policy's success model")


@app.command()
def simulate(
    trials: int = 1000,
    seed: int = 42,
    store: Path = DEFAULT_STORE,
    max_cycle: int = 3,
    max_chain: int = 4,
    policy: Path = typer.Option(DEFAULT_POLICY_FILE, help="Policy JSON file"),
) -> None:
    """Compare the count and expected plans on the same sampled transplant outcomes (read-only)."""
    check_cycle_limit(max_cycle)
    if trials < 2 or max_chain < 0:
        raise fail("--trials must be at least 2 and --max-chain at least 0.", 2)
    rules = read_policy(policy)
    with open_store(store, read_only=True) as conn:
        try:
            result = simulate_plans(conn, rules, trials, seed, max_cycle, max_chain, progress)
        except LookupError as e:
            raise fail(str(e), 2)
    for line in render_simulation(result):
        typer.echo(line)


@app.command()
def why(patient: str, store: Path = DEFAULT_STORE) -> None:
    """Explain, from graph paths, why a patient was or was not matched."""
    with open_store(store, read_only=True) as conn:
        try:
            result = explain(conn, patient.strip().upper())
        except KeyError as e:
            raise fail(str(e.args[0]), 1)
        warn_if_stale(conn)
    for line in render(result):
        typer.echo(line)


@app.command()
def whatif(withdraw: str = typer.Option(..., help="Donor id to withdraw"), store: Path = DEFAULT_STORE) -> None:
    """Re-plan as if a donor withdrew, inside a transaction that is rolled back."""
    with open_store(store, read_only=False) as conn:
        try:
            result = withdraw_donor(conn, withdraw.strip().upper(), progress)
        except (KeyError, LookupError) as e:
            raise fail(str(e.args[0]), 1)
        warn_if_stale(conn)
    typer.echo(f"If {result.donor} withdraws from {result.run}:")
    if result.leaving:
        typer.echo(f"  {result.leaving} leaves the exchange with their donor")
    if result.broken:
        for ex_id, e in result.broken:
            typer.echo(f"  {ex_id} breaks ({e.kind} of {e.size}: {', '.join(e.patients)})")
        typer.echo(f"  waiting on {result.donor} or a donor after it: {', '.join(result.stranded)}")
    else:
        typer.echo("  no chosen exchange uses this donor")
    typer.echo(f"  repair (other exchanges kept): {len(result.before)} -> {len(result.after)} patients matched")
    typer.echo(f"    lose a kidney: {', '.join(sorted(result.lost)) or 'none'}")
    typer.echo(f"    newly matched: {', '.join(sorted(result.gained)) or 'none'}")
    typer.echo(f"    same patient, different donor (needs a new crossmatch): {', '.join(sorted(result.reassigned)) or 'none'}")
    typer.echo(
        f"  full re-plan instead: {len(result.fresh.matched_patients)} patients matched, "
        f"{len(result.fresh_moved)} of them moved to a different donor"
    )
    typer.echo("  (rolled back; the store is unchanged)")


@app.command()
def counterfactual(patient: str, store: Path = DEFAULT_STORE) -> None:
    """For an unmatched patient, re-solve with single changes that might match them; antibody changes are rolled back."""
    with open_store(store, read_only=False) as conn:
        try:
            result = find_counterfactual(conn, patient.strip().upper(), progress)
        except (KeyError, LookupError, ValueError) as e:
            raise fail(str(e.args[0]), 1)
        warn_if_stale(conn)
    for line in render_counterfactual(result):
        typer.echo(line)


@app.command()
def audit(run: str = typer.Argument(None, help="Run id; the latest run if omitted"), store: Path = DEFAULT_STORE) -> None:
    """Check whether the pool still matches the inputs a run was solved on (read-only)."""
    with open_store(store, read_only=True) as conn:
        run_id = run.strip().upper() if run else latest_run_id(conn)
        if run_id is None:
            raise fail("no matching run recorded yet; run `kx match` first", 1)
        try:
            record = run_record(conn, run_id)
        except KeyError as e:
            raise fail(str(e.args[0]), 1)
        check = check_inputs(conn, run_id)
    typer.echo(f"{run_id}, created {record['created_at']}")
    typer.echo(f"  policy: {record.get('policy') or 'not recorded'} (sha256 {record.get('policy_sha256') or 'not recorded'})")
    typer.echo(f"  objective: {record['objective']}")
    typer.echo(f"  solver: {record['solver']}")
    typer.echo(f"  limits: cycles of up to {record['max_cycle']}, chains of up to {record['max_chain']}")
    typer.echo(f"  stored input hash:  {check.stored or 'not recorded (run predates input hashing)'}")
    typer.echo(f"  current input hash: {check.current}")
    if check.stored is None:
        typer.echo("  cannot tell whether inputs changed since this run")
    else:
        typer.echo("  inputs unchanged" if check.unchanged else "  inputs changed since this run")



@app.command()
def status(
    exchange: str = typer.Argument(None, help="Exchange id, such as R1-X5; omit to list"),
    set_to: str = typer.Option(None, "--set", help=f"New status: {', '.join(STATUSES)}"),
    note: str = typer.Option("", help="Why, recorded with the change"),
    store: Path = DEFAULT_STORE,
) -> None:
    """List exchanges with their status, or record what happened to one (offer, crossmatch, surgery)."""
    if set_to is None:
        if exchange is not None:
            raise fail("Give --set with an exchange id, or no arguments to list.", 2)
        with open_store(store, read_only=True) as conn:
            run = latest_run_id(conn)
            if run is None:
                raise fail("No matching run recorded yet; run `kx match` first.", 2)
            rows = statuses(conn, run)
        counts: dict[str, int] = {}
        for r in rows:
            counts[r["status"]] = counts.get(r["status"], 0) + 1
            typer.echo(f"{r['id']:<10} {r['kind']:<6} {r['size']}  {r['status']}")
        typer.echo(", ".join(f"{n} {s}" for s, n in sorted(counts.items())))
        return
    if exchange is None:
        raise fail("Give the exchange id to change.", 2)
    with open_store(store, read_only=False) as conn:
        try:
            change = set_status(conn, exchange.strip().upper(), set_to, note)
        except KeyError as e:
            raise fail(str(e.args[0]), 1)
        except TransitionError as e:
            raise fail(str(e), 2)
    typer.echo(change.summary())


if __name__ == "__main__":
    app()
