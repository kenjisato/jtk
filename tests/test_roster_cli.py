import sys

import pytest
from typer.testing import CliRunner

from conftest import GUARD_COMMAND, MASTER_TEXT, ROSTER_HEADERS, hooks, make_project
from jtk import crypto
from jtk.cli import app
from jtk.table import Table
from jtk.table.secure_io import read_encrypted_xlsx, write_encrypted_xlsx

runner = CliRunner()
SCOPE = "2026後期/経済数学I"


def make_roster(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    table = Table(
        headers=list(ROSTER_HEADERS),
        rows=[
            ["2026", "架空", "太郎", "taro@example.com", None, "たろう", None],
            ["2026", "例", "花子", "hanako@example.com", None, "はな", None],
        ],
        row_numbers=[2, 3],
    )
    write_encrypted_xlsx(table, path, "pw")
    return path


@pytest.fixture
def guarded_roster(tmp_path):
    root = make_project(tmp_path / "研究室", hooks(GUARD_COMMAND))
    return make_roster(root / "private" / "roster.xlsx")


@pytest.fixture
def plain_roster(tmp_path):
    return make_roster(tmp_path / "plain" / "roster.xlsx")


@pytest.fixture
def interactive(monkeypatch):
    monkeypatch.setattr("jtk.roster._stdin_isatty", lambda: True)
    monkeypatch.setattr("jtk.roster.getpass.getpass", lambda prompt="": "pw")


def fill(path, *extra, input_text=None):
    return runner.invoke(
        app,
        ["table", "roster", "fill", str(path), "--scope", SCOPE, *extra],
        input=input_text if input_text is not None else f"{path.name}\n",
    )


def ids_of(path):
    table = read_encrypted_xlsx(path, "pw")
    return [r[6] for r in table.rows]


def test_fill_requires_danger(guarded_roster, master_key, interactive):
    before = guarded_roster.read_bytes()
    r = fill(guarded_roster)
    assert r.exit_code == 1
    assert "--danger" in r.output
    assert guarded_roster.read_bytes() == before


def test_fill_requires_tty(guarded_roster, master_key, monkeypatch):
    monkeypatch.setattr("jtk.roster._stdin_isatty", lambda: False)
    before = guarded_roster.read_bytes()
    r = fill(guarded_roster, "--danger")
    assert r.exit_code == 1
    assert "PowerShell" in r.output
    assert guarded_roster.read_bytes() == before


def test_fill_stops_when_unguarded(plain_roster, master_key, interactive):
    before = plain_roster.read_bytes()
    r = fill(plain_roster, "--danger")
    assert r.exit_code == 1
    assert plain_roster.read_bytes() == before


def test_fill_stops_when_name_differs(guarded_roster, master_key, interactive):
    before = guarded_roster.read_bytes()
    r = fill(guarded_roster, "--danger", input_text="other.xlsx\n")
    assert r.exit_code == 1
    assert guarded_roster.read_bytes() == before


def test_fill_end_to_end(guarded_roster, master_key, interactive):
    r = fill(guarded_roster, "--danger")
    assert r.exit_code == 0, r.output
    scope_key = crypto.derive_scope_key(crypto.decode_master_key(MASTER_TEXT), SCOPE)
    expected = [crypto.hash_email(scope_key, e) for e in ("taro@example.com", "hanako@example.com")]
    assert ids_of(guarded_roster) == expected
    assert "2 件" in r.output
    assert SCOPE in r.output
    assert crypto.fingerprint(crypto.decode_master_key(MASTER_TEXT)) in r.output
    for i in expected:
        assert i in r.output
    for secret in ("架空", "太郎", "例", "花子", "example.com", "たろう"):
        assert secret not in r.output


def test_fill_nothing_to_fill_keeps_file(guarded_roster, master_key, interactive):
    assert fill(guarded_roster, "--danger").exit_code == 0
    before = guarded_roster.read_bytes()
    r = fill(guarded_roster, "--danger")
    assert r.exit_code == 0
    assert "0 件" in r.output
    assert guarded_roster.read_bytes() == before


def test_fill_wrong_password(guarded_roster, master_key, monkeypatch):
    monkeypatch.setattr("jtk.roster._stdin_isatty", lambda: True)
    monkeypatch.setattr("jtk.roster.getpass.getpass", lambda prompt="": "bad")
    before = guarded_roster.read_bytes()
    r = fill(guarded_roster, "--danger")
    assert r.exit_code == 1
    assert guarded_roster.read_bytes() == before
