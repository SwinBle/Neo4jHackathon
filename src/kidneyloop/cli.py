from pathlib import Path

import relate
import typer

from .exchanges import find_arcs, find_cycles
from .explain import explain, render
from .generator import generate_pool
from .runs import run_match
from .store import create_json_store, derive, load_pool
from .whatif import withdraw_donor

DEFAULT_STORE = Path("data/kidneyloop")

app = typer.Typer(no_args_is_help=True)


@app.callback()
def main() -> None:
    """KidneyLoop: kidney paired-donation matching on a Relate graph."""


def _require(store: Path) -> None:
    if not (store / "manifest.json").exists():
        typer.echo(f"No store at {store}. Run `kx build` first.", err=True)
        raise typer.Exit(2)


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
        typer.echo("--pairs must be at least 1 and --altruists at least 0.", err=True)
        raise typer.Exit(2)
    try:
        create_json_store(store)
    except FileExistsError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(2)
    with relate.connect(str(store)) as conn:
        load_pool(conn, generate_pool(seed, pairs, altruists))
        derive(conn)
        counts = conn.execute(
            "MATCH (p:Patient) WITH count(p) AS patients "
            "MATCH (d:Donor) WITH patients, count(d) AS donors "
            "OPTIONAL MATCH ()-[c:COMPATIBLE]->() RETURN patients, donors, count(c) AS arcs"
        ).single()
    typer.echo(f"{store}: {counts['patients']} patients, {counts['donors']} donors, {counts['arcs']} compatible arcs")


@app.command()
def options(store: Path = DEFAULT_STORE, max_cycle: int = 3) -> None:
    """Count the cycles and arcs a matching round can choose from."""
    _require(store)
    with relate.connect(str(store), read_only=True) as conn:
        cycles = find_cycles(conn, max_cycle)
        arcs = find_arcs(conn)
    for size in range(2, max_cycle + 1):
        typer.echo(f"{size}-way cycles: {sum(c.size == size for c in cycles)}")
    typer.echo(f"altruistic arcs: {sum(a.altruistic for a in arcs)}, pair arcs: {sum(not a.altruistic for a in arcs)}")


@app.command()
def match(store: Path = DEFAULT_STORE, max_cycle: int = 3, max_chain: int = 4) -> None:
    """Choose disjoint cycles and chains and record them as a new MatchRun."""
    _require(store)
    if not 2 <= max_cycle <= 3 or max_chain < 0:
        typer.echo("--max-cycle must be 2 or 3 and --max-chain at least 0.", err=True)
        raise typer.Exit(2)
    with relate.connect(str(store)) as conn:
        run = run_match(conn, max_cycle, max_chain)
        patients = conn.execute("MATCH (p:Patient) RETURN count(p) AS n").single()["n"]
    cycles = [e for e in run.plan.exchanges if e.kind == "cycle"]
    chains = [e for e in run.plan.exchanges if e.kind == "chain"]
    typer.echo(f"{run.id}: {run.plan.transplants} transplants for {patients} patients")
    typer.echo(f"  from {run.candidate_cycles} candidate cycles and {run.candidate_arcs} arcs")
    typer.echo(f"  {len(cycles)} cycles, {len(chains)} chains, {patients - len(run.plan.matched_patients)} patients unmatched")


@app.command()
def why(patient: str, store: Path = DEFAULT_STORE) -> None:
    """Explain, from graph paths, why a patient was or was not matched."""
    _require(store)
    with relate.connect(str(store), read_only=True) as conn:
        try:
            result = explain(conn, patient)
        except KeyError as e:
            typer.echo(str(e.args[0]), err=True)
            raise typer.Exit(1)
    for line in render(result):
        typer.echo(line)


@app.command()
def whatif(withdraw: str = typer.Option(..., help="Donor id to withdraw"), store: Path = DEFAULT_STORE) -> None:
    """Re-plan as if a donor withdrew, inside a transaction that is rolled back."""
    _require(store)
    with relate.connect(str(store)) as conn:
        try:
            result = withdraw_donor(conn, withdraw)
        except (KeyError, LookupError) as e:
            typer.echo(str(e.args[0]), err=True)
            raise typer.Exit(1)
    typer.echo(f"If {result.donor} withdraws from {result.run}:")
    if result.broken:
        for ex_id, e in result.broken:
            typer.echo(f"  {ex_id} breaks ({e.kind} of {e.size}: {', '.join(e.patients)})")
        typer.echo(f"  waiting on {result.donor} or a donor after it: {', '.join(result.stranded)}")
    else:
        typer.echo("  no chosen exchange uses this donor")
    typer.echo(f"  re-plan: {len(result.before)} -> {len(result.after)} patients matched")
    typer.echo(f"  lose a kidney: {', '.join(sorted(result.lost)) or 'none'}")
    typer.echo(f"  newly matched: {', '.join(sorted(result.gained)) or 'none'}")
    typer.echo("  (rolled back; the store is unchanged)")


if __name__ == "__main__":
    app()
