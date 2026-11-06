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


def _encrypt_workbook(wb, path):
    from io import BytesIO

    from msoffcrypto.format.ooxml import OOXMLFile

    plain = BytesIO()
    wb.save(plain)
    plain.seek(0)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as out:
        OOXMLFile(plain).encrypt("pw", out)


def _decrypt_workbook(path):
    from io import BytesIO

    import msoffcrypto
    from openpyxl import load_workbook

    with path.open("rb") as fp:
        office = msoffcrypto.OfficeFile(fp)
        office.load_key(password="pw")
        plain = BytesIO()
        office.decrypt(plain)
    plain.seek(0)
    return load_workbook(plain)


@pytest.fixture
def rich_roster(tmp_path):
    from openpyxl import Workbook
    from openpyxl.comments import Comment
    from openpyxl.styles import Font

    root = make_project(tmp_path / "研究室", hooks(GUARD_COMMAND))
    wb = Workbook()
    ws = wb.active
    ws.title = "名簿"
    ws.append(list(ROSTER_HEADERS))
    ws.append(["2026", "架空", "太郎", "taro@example.com", None, "たろう", None])
    # 3 行目は空行
    ws.cell(row=4, column=1, value="2026")
    for col, value in enumerate(["例", "花子", "hanako@example.com", None, "はな", None], start=2):
        ws.cell(row=4, column=col, value=value)
    ws["A1"].font = Font(bold=True)
    ws["B2"].comment = Comment("架空のメモ", "教員")
    ws.column_dimensions["D"].width = 30
    other = wb.create_sheet("集計")
    other["A1"] = "人数"
    other["B1"] = "=COUNTA(名簿!D2:D100)"
    other["A2"] = 42
    path = root / "private" / "roster.xlsx"
    _encrypt_workbook(wb, path)
    return path


def test_fill_keeps_workbook(rich_roster, master_key, interactive):
    r = fill(rich_roster, "--danger")
    assert r.exit_code == 0, r.output
    scope_key = crypto.derive_scope_key(crypto.decode_master_key(MASTER_TEXT), SCOPE)

    wb = _decrypt_workbook(rich_roster)
    assert wb.sheetnames == ["名簿", "集計"]
    ws = wb["名簿"]
    assert [c.value for c in ws[1]] == list(ROSTER_HEADERS)
    assert ws["A1"].font.bold
    assert ws["B2"].comment is not None and ws["B2"].comment.text == "架空のメモ"
    assert ws.column_dimensions["D"].width == 30
    assert all(c.value is None for c in ws[3])
    assert ws["B4"].value == "例"
    assert ws["G2"].value == crypto.hash_email(scope_key, "taro@example.com")
    assert ws["G4"].value == crypto.hash_email(scope_key, "hanako@example.com")
    assert ws["G2"].data_type == "s" and ws["G4"].data_type == "s"
    assert ws.max_row == 4
    other = wb["集計"]
    assert other["B1"].value == "=COUNTA(名簿!D2:D100)"
    assert other["A2"].value == 42
