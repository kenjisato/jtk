
import pytest

from jtk import roster
from jtk.crypto import derive_scope_key, hash_email
from jtk.errors import JtkError
from jtk.roster import (
    COLUMNS,
    fill_ids,
    open_roster,
    prompt_password,
    require_tty,
    roster_column_indices,
    save_roster,
    students_from_table,
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


@pytest.fixture
def scope_key():
    return derive_scope_key(bytes(range(32)), "roster")


@pytest.fixture
def table_two_rows(scope_key):
    return Table(
        headers=list(COLUMNS),
        rows=[
            ["2026", "架空", "太郎", "taro@example.com", None, "たろう",
             hash_email(scope_key, "taro@example.com")],
            ["2026", "例", "花子", "Kakuu2@example.com", None, "はな", None],
        ],
        row_numbers=[2, 3],
    )


def test_fill_only_empty(table_two_rows, scope_key):
    before = table_two_rows.rows[0][6]
    filled = fill_ids(table_two_rows, scope_key)
    assert filled == [hash_email(scope_key, "kakuu2@example.com")]
    assert table_two_rows.rows[0][6] == before
    assert table_two_rows.rows[1][6] == filled[0]


def test_fill_works_with_reordered_columns(scope_key):
    # 見出しは逆順: ID, 呼称, 別表記, メールアドレス, 名, 姓, 学年
    table = Table(
        list(reversed(COLUMNS)),
        [[None, "はな", None, "h@example.com", "花子", "例", "2026"]],
        [2],
    )
    expected = hash_email(scope_key, "h@example.com")
    assert fill_ids(table, scope_key) == [expected]
    assert table.rows[0][0] == expected


def test_fill_accepts_extra_memo_column(table_two_rows, scope_key):
    table_two_rows.headers.append("メモ")
    for r in table_two_rows.rows:
        r.append("メモ内容")
    assert len(fill_ids(table_two_rows, scope_key)) == 1


def test_fill_rejects_mismatched_id(table_two_rows, scope_key):
    table_two_rows.rows[0][6] = "0000000000000000"
    with pytest.raises(JtkError, match="行 2 の ID がメールアドレスと合いません"):
        fill_ids(table_two_rows, scope_key)


def test_fill_rejects_empty_email(table_two_rows, scope_key):
    table_two_rows.rows[1][3] = None
    with pytest.raises(JtkError, match="行 3"):
        fill_ids(table_two_rows, scope_key)


def test_fill_rejects_duplicate_email_after_normalizing(table_two_rows, scope_key):
    table_two_rows.rows[1][3] = " TARO@example.com "
    with pytest.raises(JtkError, match="行 3"):
        fill_ids(table_two_rows, scope_key)


def test_fill_rejects_duplicate_alias_in_cohort(table_two_rows, scope_key):
    table_two_rows.rows[1][5] = "たろう"
    with pytest.raises(JtkError, match="行 3"):
        fill_ids(table_two_rows, scope_key)


def test_fill_allows_same_alias_in_other_cohort(table_two_rows, scope_key):
    table_two_rows.rows[1][5] = "たろう"
    table_two_rows.rows[1][0] = "2027"
    assert len(fill_ids(table_two_rows, scope_key)) == 1


def test_fill_rejects_empty_alias(table_two_rows, scope_key):
    table_two_rows.rows[1][5] = ""
    with pytest.raises(JtkError, match="行 3"):
        fill_ids(table_two_rows, scope_key)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda t: t.rows[1].__setitem__(3, None),
        lambda t: t.rows[1].__setitem__(3, "taro@example.com"),
        lambda t: t.rows[0].__setitem__(6, "0000000000000000"),
        lambda t: t.rows[1].__setitem__(5, "たろう"),
        lambda t: t.rows[1].__setitem__(5, None),
    ],
)
def test_fill_failure_leaves_table_unchanged_and_message_has_no_values(
    table_two_rows, scope_key, mutate
):
    table_two_rows.rows[1][1] = "秘密姓"
    table_two_rows.rows[1][2] = "秘密名"
    mutate(table_two_rows)
    snapshot = [list(r) for r in table_two_rows.rows]
    with pytest.raises(JtkError) as exc:
        fill_ids(table_two_rows, scope_key)
    assert table_two_rows.rows == snapshot
    msg = str(exc.value)
    for secret in ("秘密姓", "秘密名", "example.com", "たろう", "0000000000000000"):
        assert secret not in msg


def test_numeric_cells_become_text_without_dot_zero():
    table = Table(
        list(COLUMNS),
        [[2026.0, "架空", "太郎", "t@example.com", None, "たろう", None]],
        [2],
    )
    assert students_from_table(table)[0].cohort == "2026"
    assert roster._text(1.5) == "1.5"


def test_save_roster_roundtrip(tmp_path, table_two_rows, scope_key):
    fill_ids(table_two_rows, scope_key)
    path = tmp_path / "r.xlsx"
    save_roster(table_two_rows, path, "pw")
    assert open_roster(path, "pw")[1].id == hash_email(scope_key, "kakuu2@example.com")


def test_save_roster_keeps_original_on_failure(monkeypatch, roster_file):
    import os

    original = roster_file.read_bytes()

    def deny(src, dst):
        raise PermissionError("secret-cell-value")

    monkeypatch.setattr(os, "replace", deny)
    table = Table(list(COLUMNS), [["2026", "秘密姓", "名", "a@example.com", None, "x", None]], [2])
    with pytest.raises(JtkError) as exc:
        save_roster(table, roster_file, "pw")
    assert "秘密姓" not in str(exc.value) and "secret-cell-value" not in str(exc.value)
    assert roster_file.read_bytes() == original
    assert [p.name for p in roster_file.parent.iterdir()] == [roster_file.name]
