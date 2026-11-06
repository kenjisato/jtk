
import pytest

from jtk import roster
from jtk.errors import JtkError
from jtk.roster import (
    COLUMNS,
    open_roster,
    prompt_password,
    require_tty,
    roster_column_indices,
)
from jtk.table import Table
from jtk.table.secure_io import WrongPassword, write_encrypted_xlsx


def test_open_roster_parses_columns(roster_file):
    s = open_roster(roster_file, "pw")
    assert len(s) == 2
    assert s[0].family == "架空" and s[0].variants == ["かくう", "Kakuu"] and s[0].id == ""
    assert s[0].row == 2 and s[0].cohort == "2026" and s[0].alias == "たろう"
    assert s[1].variants == [] and s[1].id == "abcd1234abcd1234"


def test_variants_split_on_both_commas_and_strip():
    from jtk.roster import _split_variants

    assert _split_variants(" a , b、 c ,, ") == ["a", "b", "c"]


def test_open_roster_missing_column(tmp_path):
    headers = [c for c in COLUMNS if c != "呼称"]
    path = tmp_path / "r.xlsx"
    write_encrypted_xlsx(Table(headers, [["x"] * 6], [2]), path, "pw")
    with pytest.raises(JtkError, match="列 呼称 がありません"):
        open_roster(path, "pw")


def test_column_indices_follow_header_order():
    idx = roster_column_indices(list(reversed(COLUMNS)))
    assert idx["ID"] == 0 and idx["学年"] == 6


def test_open_roster_wrong_password(roster_file):
    with pytest.raises(WrongPassword):
        open_roster(roster_file, "x")


def test_require_tty_message_mentions_powershell(monkeypatch):
    monkeypatch.setattr(roster, "_stdin_isatty", lambda: False)
    with pytest.raises(JtkError, match="PowerShell"):
        require_tty()


def test_prompt_password_confirm(monkeypatch):
    monkeypatch.setattr(roster, "_stdin_isatty", lambda: True)
    answers = iter(["pw", "pw"])
    monkeypatch.setattr(roster.getpass, "getpass", lambda prompt="": next(answers))
    assert prompt_password(confirm=True) == "pw"


def test_prompt_password_confirm_mismatch(monkeypatch):
    monkeypatch.setattr(roster, "_stdin_isatty", lambda: True)
    answers = iter(["pw", "other"])
    monkeypatch.setattr(roster.getpass, "getpass", lambda prompt="": next(answers))
    with pytest.raises(JtkError, match="一致しません"):
        prompt_password(confirm=True)


def test_prompt_password_requires_tty(monkeypatch):
    monkeypatch.setattr(roster, "_stdin_isatty", lambda: False)
    with pytest.raises(JtkError, match="PowerShell"):
        prompt_password()
