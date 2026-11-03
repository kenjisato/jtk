import typer

from jtk import keys

app = typer.Typer(help="授業の成績・名簿まわりの小さな道具", pretty_exceptions_enable=False)
app.add_typer(keys.app, name="key")
