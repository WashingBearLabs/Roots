import typer

app = typer.Typer()


@app.callback()
def main() -> None:
    """Roots — A process orchestration framework."""


if __name__ == "__main__":
    app()
