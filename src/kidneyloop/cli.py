from pathlib import Path

import relate
import typer

from .exchanges import find_arcs, find_cycles
from .generator import generate_pool
from .store import create_json_store, derive, load_pool

DEFAULT_STORE = Path("data/kidneyloop")

app = typer.Typer(no_args_is_help=True)


@app.callback()
def main() -> None:
    """KidneyLoop: kidney paired-donation matching on a Relate graph."""


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
    create_json_store(store)
    with relate.connect(str(store)) as conn:
        load_pool(conn, generate_pool(seed, pairs, altruists))
        derive(conn)
        counts = conn.execute(
            "MATCH (p:Patient) WITH count(p) AS patients "
            "MATCH (d:Donor) WITH patients, count(d) AS donors "
            "MATCH ()-[c:COMPATIBLE]->() RETURN patients, donors, count(c) AS arcs"
        ).single()
    typer.echo(f"{store}: {counts['patients']} patients, {counts['donors']} donors, {counts['arcs']} compatible arcs")


@app.command()
def options(store: Path = DEFAULT_STORE, max_cycle: int = 3) -> None:
    """Count the cycles and arcs a matching round can choose from."""
    with relate.connect(str(store), read_only=True) as conn:
        cycles = find_cycles(conn, max_cycle)
        arcs = find_arcs(conn)
    for size in range(2, max_cycle + 1):
        typer.echo(f"{size}-way cycles: {sum(c.size == size for c in cycles)}")
    typer.echo(f"altruistic arcs: {sum(a.altruistic for a in arcs)}, pair arcs: {sum(not a.altruistic for a in arcs)}")
