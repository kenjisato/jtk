import typer

from jtk import keys
from jtk.guard import guard_command
from jtk.table.cli import app as table_app

app = typer.Typer(help="授業の成績・名簿まわりの小さな道具", pretty_exceptions_enable=False)
app.add_typer(keys.app, name="key")
app.add_typer(table_app, name="table")
app.command("guard")(guard_command)
