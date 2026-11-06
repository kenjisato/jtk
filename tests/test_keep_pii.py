"""`pii hash --keep-pii --encrypt` と、`--encrypt`・暗号化された入力のテスト。"""

import os
from io import BytesIO

import msoffcrypto
import pytest
from msoffcrypto.format.ooxml import OOXMLFile
from openpyxl import Workbook, load_workbook
from typer.testing import CliRunner

from conftest import GUARD_COMMAND, MASTER_TEXT, hooks, make_project
from jtk import crypto, prompt
from jtk.cli import app
from jtk.errors import JtkError
from jtk.prompt import prompt_password, require_tty
from jtk.table import Table
from jtk.table.io import write_table
from jtk.table.secure_io import read_encrypted_xlsx, write_encrypted_xlsx

runner = CliRunner()
SCOPE = "Thesis"
HEADERS = ["学年", "姓", "名", "メールアドレス", "ID"]
SECRETS = ("架空", "太郎", "例", "花子", "example.com")


def scope_key():
    return crypto.derive_scope_key(crypto.decode_master_key(MASTER_TEXT), SCOPE)


def h(email):
    return crypto.hash_email(scope_key(), email)


def rows(ids=(None, None)):
    return [
        ["2026", "架空", "太郎", "taro@example.com", ids[0]],
        ["2026", "例", "花子", "hanako@example.com", ids[1]],
    ]


def make_ids(path, headers=HEADERS, data=None, password="pw"):
    path.parent.mkdir(parents=True, exist_ok=True)
    table = Table(list(headers), data if data is not None else rows(), [2, 3])
    if password is None:
        write_table(table, path)
    else:
        write_encrypted_xlsx(table, path, password)
    return path


@pytest.fixture
def project(tmp_path):
    return make_project(tmp_path / "研究室", hooks(GUARD_COMMAND))


@pytest.fixture
def guarded(project):
    return make_ids(project / "private" / "student-ids.xlsx")


@pytest.fixture
def unguarded(tmp_path):
    return make_ids(tmp_path / "plain" / "student-ids.xlsx")


@pytest.fixture
def passwords(monkeypatch):
    """端末での実行にし、getpass への答えを順に返す。呼ばれた回数を数える。"""
    monkeypatch.setattr(prompt, "_stdin_isatty", lambda: True)
    state = {"answers": ["pw"], "calls": 0}

    def fake(prompt_text=""):
        state["calls"] += 1
        answers = state["answers"]
        return answers.pop(0) if len(answers) > 1 else answers[0]

    monkeypatch.setattr(prompt.getpass, "getpass", fake)
    return state


def keep(path, *extra, out=None, input_text=None):
    out = path if out is None else out
    args = [
        "table", "pii", "hash", str(path), "--scope", SCOPE,
        "--hash", "メールアドレス", "--hash-column", "ID",
        "--keep-pii", "--encrypt", "--out", str(out), "--force", *extra,
    ]
    return runner.invoke(app, args, input=input_text if input_text is not None else f"{out.name}\n")


def ids_of(path, column=4, password="pw"):
    return [r[column] for r in read_encrypted_xlsx(path, password).rows]


def assert_no_secrets(text):
    for secret in SECRETS:
        assert secret not in text


# --- prompt ---


def test_require_tty_message_mentions_powershell(monkeypatch):
    monkeypatch.setattr(prompt, "_stdin_isatty", lambda: False)
    with pytest.raises(JtkError, match="PowerShell"):
        require_tty()


def test_prompt_password_confirm(monkeypatch):
    monkeypatch.setattr(prompt, "_stdin_isatty", lambda: True)
    answers = iter(["pw", "pw"])
    monkeypatch.setattr(prompt.getpass, "getpass", lambda p="": next(answers))
    assert prompt_password(confirm=True) == "pw"


def test_prompt_password_confirm_mismatch(monkeypatch):
    monkeypatch.setattr(prompt, "_stdin_isatty", lambda: True)
    answers = iter(["pw", "other"])
    monkeypatch.setattr(prompt.getpass, "getpass", lambda p="": next(answers))
    with pytest.raises(JtkError, match="一致しません"):
        prompt_password(confirm=True)


def test_prompt_password_requires_tty(monkeypatch):
    monkeypatch.setattr(prompt, "_stdin_isatty", lambda: False)
    with pytest.raises(JtkError, match="PowerShell"):
        prompt_password()


# --- 守り ---


def test_keep_pii_requires_encrypt(guarded, master_key, passwords):
    before = guarded.read_bytes()
    args = ["table", "pii", "hash", str(guarded), "--scope", SCOPE, "--hash", "メールアドレス",
            "--hash-column", "ID", "--keep-pii", "--out", str(guarded), "--force"]
    r = runner.invoke(app, args, input=f"{guarded.name}\n")
    assert r.exit_code == 1
    assert "--encrypt" in r.output
    assert guarded.read_bytes() == before
    assert passwords["calls"] == 0


def test_keep_pii_requires_tty(guarded, master_key, monkeypatch):
    monkeypatch.setattr(prompt, "_stdin_isatty", lambda: False)
    before = guarded.read_bytes()
    r = keep(guarded)
    assert r.exit_code == 1
    assert "PowerShell" in r.output
    assert guarded.read_bytes() == before


def test_keep_pii_stops_when_unguarded(unguarded, master_key, passwords):
    before = unguarded.read_bytes()
    r = keep(unguarded)
    assert r.exit_code == 1
    assert "フック" in r.output
    assert unguarded.read_bytes() == before
    assert passwords["calls"] == 0


def test_keep_pii_stops_when_name_differs(guarded, master_key, passwords):
    before = guarded.read_bytes()
    r = keep(guarded, input_text="other.xlsx\n")
    assert r.exit_code == 1
    assert guarded.read_bytes() == before
    assert passwords["calls"] == 0


def test_keep_pii_wrong_password(guarded, master_key, passwords):
    passwords["answers"] = ["bad"]
    before = guarded.read_bytes()
    r = keep(guarded)
    assert r.exit_code == 1
    assert "パスワードが違います" in r.output
    assert guarded.read_bytes() == before


def test_keep_pii_is_not_a_config_key(guarded, master_key, passwords, tmp_path):
    cfg = tmp_path / "jtk.toml"
    cfg.write_text("[jtk.table.pii]\nkeep_pii = true\n", encoding="utf-8")
    before = guarded.read_bytes()
    r = keep(guarded, "--config", cfg)
    assert r.exit_code == 1
    assert guarded.read_bytes() == before


# --- パス ---


def test_same_out_requires_force(guarded, master_key, passwords):
    before = guarded.read_bytes()
    args = ["table", "pii", "hash", str(guarded), "--scope", SCOPE, "--hash", "メールアドレス",
            "--hash-column", "ID", "--keep-pii", "--encrypt", "--out", str(guarded)]
    r = runner.invoke(app, args, input=f"{guarded.name}\n")
    assert r.exit_code == 1
    assert "--out が入力と同じ場所です" in r.output
    assert guarded.read_bytes() == before


def test_same_out_forbids_move_original(guarded, master_key, passwords, project):
    before = guarded.read_bytes()
    r = keep(guarded, "--move-original", project / "private" / "old" / "x.xlsx")
    assert r.exit_code == 1
    assert "--move-original" in r.output
    assert guarded.read_bytes() == before


def test_same_out_needs_keep_pii(guarded, master_key, passwords):
    # --encrypt だけでは入力を上書きできない
    args = ["table", "pii", "hash", str(guarded), "--scope", SCOPE, "--hash", "メールアドレス",
            "--encrypt", "--out", str(guarded), "--force"]
    before = guarded.read_bytes()
    r = runner.invoke(app, args)
    assert r.exit_code == 1
    assert "--out が入力と同じ場所です" in r.output
    assert guarded.read_bytes() == before


@pytest.mark.parametrize("cmd", ["hash", "drop"])
def test_encrypt_rejects_csv(cmd, forms_csv, master_key, passwords, tmp_path):
    out = tmp_path / "o.csv"
    extra = ["--scope", SCOPE] if cmd == "hash" else []
    r = runner.invoke(app, ["table", "pii", cmd, str(forms_csv()), "--preset", "msforms",
                            *extra, "--encrypt", "--out", str(out)])
    assert r.exit_code == 1
    assert ".xlsx" in r.output
    assert not out.exists()


# --- hash 列 ---


def test_end_to_end_fills_in_place(guarded, master_key, passwords):
    r = keep(guarded)
    assert r.exit_code == 0, r.output
    expected = [h("taro@example.com"), h("hanako@example.com")]
    assert ids_of(guarded) == expected
    table = read_encrypted_xlsx(guarded, "pw")
    assert [row[3] for row in table.rows] == ["taro@example.com", "hanako@example.com"]
    assert "埋めた件数: 2 件" in r.output
    assert SCOPE in r.output
    assert crypto.fingerprint(crypto.decode_master_key(MASTER_TEXT)) in r.output
    assert "漏れの検査: しませんでした（--keep-pii）" in r.output
    assert_no_secrets(r.output)
    for value in expected:
        assert value not in r.output
    assert passwords["calls"] == 1  # 入力のパスワードをそのまま使う
    assert sorted(p.name for p in guarded.parent.iterdir()) == ["r.xlsx", "student-ids.xlsx"]


def test_fills_only_empty(project, master_key, passwords):
    path = make_ids(project / "private" / "ids.xlsx", data=rows((h("taro@example.com"), None)))
    r = keep(path)
    assert r.exit_code == 0, r.output
    assert ids_of(path) == [h("taro@example.com"), h("hanako@example.com")]
    assert "埋めた件数: 1 件" in r.output


def test_mismatch_stops_with_row_number_only(project, master_key, passwords):
    path = make_ids(project / "private" / "ids.xlsx", data=rows(("0000000000000000", None)))
    before = path.read_bytes()
    r = keep(path)
    assert r.exit_code == 1
    assert "行 2" in r.output
    assert "0000000000000000" not in r.output
    assert h("hanako@example.com") not in r.output
    assert_no_secrets(r.output)
    assert path.read_bytes() == before


def test_nothing_to_fill_keeps_file(guarded, master_key, passwords):
    assert keep(guarded).exit_code == 0
    before = guarded.read_bytes()
    r = keep(guarded)
    assert r.exit_code == 0, r.output
    assert "埋めた件数: 0 件" in r.output
    assert guarded.read_bytes() == before


def test_missing_column_is_added_at_right_end(project, master_key, passwords):
    path = make_ids(project / "private" / "ids.xlsx", headers=HEADERS[:4],
                    data=[r[:4] for r in rows()])
    r = keep(path)
    assert r.exit_code == 0, r.output
    table = read_encrypted_xlsx(path, "pw")
    assert table.headers == HEADERS
    assert [row[4] for row in table.rows] == [h("taro@example.com"), h("hanako@example.com")]
    assert "追加した列: ID" in r.output


def test_hash_column_same_as_email_column_stops(guarded, master_key, passwords):
    before = guarded.read_bytes()
    args = ["table", "pii", "hash", str(guarded), "--scope", SCOPE, "--hash", "メールアドレス",
            "--hash-column", "メールアドレス", "--keep-pii", "--encrypt", "--out", str(guarded),
            "--force"]
    r = runner.invoke(app, args, input=f"{guarded.name}\n")
    assert r.exit_code == 1
    assert guarded.read_bytes() == before


def test_omit_is_rejected_with_keep_pii(guarded, master_key, passwords):
    before = guarded.read_bytes()
    r = keep(guarded, "--omit", "学年")
    assert r.exit_code == 1
    assert "--omit" in r.output
    assert guarded.read_bytes() == before


def test_plain_input_asks_password_twice(project, master_key, passwords):
    src = make_ids(project / "private" / "plain.xlsx", password=None)
    out = project / "private" / "ids.xlsx"
    passwords["answers"] = ["new", "new"]
    r = keep(src, out=out)
    assert r.exit_code == 0, r.output
    assert passwords["calls"] == 2
    assert ids_of(out, password="new") == [h("taro@example.com"), h("hanako@example.com")]
    assert src.exists()


def test_plain_input_password_mismatch_writes_nothing(project, master_key, passwords):
    src = make_ids(project / "private" / "plain.xlsx", password=None)
    out = project / "private" / "ids.xlsx"
    passwords["answers"] = ["new", "other"]
    r = keep(src, out=out)
    assert r.exit_code == 1
    assert "一致しません" in r.output
    assert not out.exists()


def _encrypt_workbook(wb, path):
    plain = BytesIO()
    wb.save(plain)
    plain.seek(0)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as out:
        OOXMLFile(plain).encrypt("pw", out)


def _decrypt_workbook(path):
    with path.open("rb") as fp:
        office = msoffcrypto.OfficeFile(fp)
        office.load_key(password="pw")
        plain = BytesIO()
        office.decrypt(plain)
    plain.seek(0)
    return load_workbook(plain)


@pytest.fixture
def rich_ids(project):
    from openpyxl.comments import Comment
    from openpyxl.styles import Font

    wb = Workbook()
    ws = wb.active
    ws.title = "名簿"
    ws.append(HEADERS)
    ws.append(["2026", "架空", "太郎", "taro@example.com", None])
    # 3 行目は空行
    for col, value in enumerate(["2026", "例", "花子", "hanako@example.com", None], start=1):
        ws.cell(row=4, column=col, value=value)
    ws["A1"].font = Font(bold=True)
    ws["B2"].comment = Comment("架空のメモ", "教員")
    ws.column_dimensions["D"].width = 30
    other = wb.create_sheet("集計")
    other["A1"] = "人数"
    other["B1"] = "=COUNTA(名簿!D2:D100)"
    other["A2"] = 42
    path = project / "private" / "student-ids.xlsx"
    _encrypt_workbook(wb, path)
    return path


def test_keeps_workbook(rich_ids, master_key, passwords):
    r = keep(rich_ids)
    assert r.exit_code == 0, r.output
    wb = _decrypt_workbook(rich_ids)
    assert wb.sheetnames == ["名簿", "集計"]
    ws = wb["名簿"]
    assert [c.value for c in ws[1]] == HEADERS
    assert ws["A1"].font.bold
    assert ws["B2"].comment is not None and ws["B2"].comment.text == "架空のメモ"
    assert ws.column_dimensions["D"].width == 30
    assert all(c.value is None for c in ws[3])
    assert ws["B4"].value == "例"
    assert ws["E2"].value == h("taro@example.com")
    assert ws["E4"].value == h("hanako@example.com")
    assert ws["E2"].data_type == "s" and ws["E4"].data_type == "s"
    assert ws.max_row == 4
    other = wb["集計"]
    assert other["B1"].value == "=COUNTA(名簿!D2:D100)"
    assert other["A2"].value == 42


def test_original_kept_when_replace_fails(guarded, master_key, passwords, monkeypatch):
    before = guarded.read_bytes()

    def deny(src, dst):
        raise PermissionError("taro@example.com")

    monkeypatch.setattr(os, "replace", deny)
    r = keep(guarded)
    assert r.exit_code == 1
    assert_no_secrets(r.output)
    assert guarded.read_bytes() == before
    assert sorted(p.name for p in guarded.parent.iterdir()) == ["r.xlsx", "student-ids.xlsx"]


# --- --encrypt だけ、暗号化された入力 ---


def test_hash_encrypt_drops_pii_and_encrypts(forms_xlsx, master_key, passwords, tmp_path):
    out = tmp_path / "out" / "o.xlsx"
    passwords["answers"] = ["new", "new"]
    r = runner.invoke(app, ["table", "pii", "hash", str(forms_xlsx()), "--preset", "msforms",
                            "--scope", SCOPE, "--encrypt", "--out", str(out)])
    assert r.exit_code == 0, r.output
    table = read_encrypted_xlsx(out, "new")
    assert table.headers == ["ID", "Start time", "Completion time", "hash", "Q1"]
    assert "漏れの検査: 見つかりませんでした" in r.output
    assert passwords["calls"] == 2


def test_encrypt_requires_tty(forms_xlsx, master_key, monkeypatch, tmp_path):
    monkeypatch.setattr(prompt, "_stdin_isatty", lambda: False)
    out = tmp_path / "o.xlsx"
    r = runner.invoke(app, ["table", "pii", "drop", str(forms_xlsx()), "--preset", "msforms",
                            "--encrypt", "--out", str(out)])
    assert r.exit_code == 1
    assert "PowerShell" in r.output
    assert not out.exists()


def test_drop_reads_encrypted_input(guarded, passwords, tmp_path):
    out = tmp_path / "o.xlsx"
    r = runner.invoke(app, ["table", "pii", "drop", str(guarded), "--pii", "姓", "--pii", "名",
                            "--pii", "メールアドレス", "--out", str(out)])
    assert r.exit_code == 0, r.output
    wb = load_workbook(out)  # --encrypt が無ければ平文で書く
    got = [list(row) for row in wb.active.iter_rows(values_only=True)]
    wb.close()
    assert got == [["学年", "ID"], ["2026", None], ["2026", None]]
    assert passwords["calls"] == 1


def test_drop_encrypt_reuses_input_password(guarded, passwords, tmp_path):
    out = tmp_path / "o.xlsx"
    r = runner.invoke(app, ["table", "pii", "drop", str(guarded), "--pii", "姓", "--pii", "名",
                            "--pii", "メールアドレス", "--encrypt", "--out", str(out)])
    assert r.exit_code == 0, r.output
    assert read_encrypted_xlsx(out, "pw").headers == ["学年", "ID"]
    assert passwords["calls"] == 1


def test_encrypted_input_wrong_password(guarded, passwords, tmp_path):
    passwords["answers"] = ["bad"]
    out = tmp_path / "o.xlsx"
    r = runner.invoke(app, ["table", "pii", "drop", str(guarded), "--pii", "姓", "--out", str(out)])
    assert r.exit_code == 1
    assert "パスワードが違います" in r.output
    assert not out.exists()
