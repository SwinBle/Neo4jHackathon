import relate
import typer

app = typer.Typer(no_args_is_help=True)


@app.callback()
def main() -> None:
    """KidneyLoop: kidney paired-donation matching on a Relate graph."""


@app.command()
def version() -> None:
    """Print the KidneyLoop and Relate engine versions."""
    typer.echo(f"kidneyloop 0.1.0, relate {relate.__version__}")
