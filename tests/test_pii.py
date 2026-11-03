import pytest

from jtk import crypto
from jtk.crypto import derive_scope_key
from jtk.errors import JtkError
from jtk.table import Table
from jtk.table.pii import Roles, resolve_roles, transform

EN = ["ID", "Start time", "Email", "Name", "Q1"]
JA = ["ID", "開始時刻", "メール", "名前", "Q1"]
scope_key = derive_scope_key(bytes(range(32)), "2026後期/経済数学I")


def roles(headers, mode="hash", preset="msforms", hash_cols=(), pii=(), omit=()):
    return resolve_roles(
        headers,
        mode=mode,
        preset=preset,
        hash_cols=list(hash_cols),
        pii=list(pii),
        omit=list(omit),
    )


def test_preset_hash_english():
    assert roles(EN) == Roles("Email", ["Name"], [])


def test_preset_hash_japanese():
    assert roles(JA) == Roles("メール", ["名前"], [])


def test_preset_drop():
    assert roles(EN, mode="drop") == Roles(None, ["Email", "Name"], [])


def test_preset_plus_extra():
    assert roles(EN, pii=["Q1"], omit=["ID"]) == Roles("Email", ["Name", "Q1"], ["ID"])


def test_preset_missing_column_error():
    with pytest.raises(JtkError, match="msforms"):
        roles(["ID", "Q1"])


def test_preset_both_languages_error():
    with pytest.raises(JtkError):
        roles(EN + ["メール"])


def test_preset_with_hash_error():
    with pytest.raises(JtkError):
        roles(EN, hash_cols=["Email"])


def test_hash_mode_without_hash_error():
    with pytest.raises(JtkError):
        roles(EN, preset=None)


def test_two_hash_cols_error():
    with pytest.raises(JtkError):
        roles(EN, preset=None, hash_cols=["Email", "Name"])


def test_drop_mode_nothing_to_drop_error():
    with pytest.raises(JtkError, match="消す列が指定されていません"):
        roles(EN, mode="drop", preset=None)


def test_drop_mode_without_preset():
    assert roles(EN, mode="drop", preset=None, pii=["Name"], omit=["ID"]) == Roles(
        None, ["Name"], ["ID"]
    )


def test_unknown_column_error():
    with pytest.raises(JtkError, match="Nope"):
        roles(EN, pii=["Nope"])


def test_duplicate_header_error():
    with pytest.raises(JtkError, match="Name"):
        roles(["ID", "Name", "Name"], mode="drop", preset=None, pii=["Name"])


def test_same_column_two_roles_error():
    with pytest.raises(JtkError, match="Q1"):
        roles(EN, pii=["Q1"], omit=["Q1"])


def _t():
    return Table(
        ["ID", "Email", "Name", "Q1"],
        [
            [1, "taro@example.com", "架空 太郎", "a"],
            [2, " Taro@Example.com ", "架空 太郎", "b"],
        ],
        [2, 3],
        "Sheet1",
    )


def test_hash_column_takes_email_position():
    out = transform(_t(), Roles("Email", ["Name"], []), scope_key=scope_key)
    assert out.headers == ["ID", "hash", "Q1"]
    assert out.rows == [[1, "a4cd517475a2aba2", "a"], [2, "a4cd517475a2aba2", "b"]]
    assert out.row_numbers == [2, 3]
    assert out.sheet_title == "Sheet1"


def test_drop_only():
    out = transform(_t(), Roles(None, ["Email", "Name"], ["ID"]), scope_key=None)
    assert out.headers == ["Q1"]
    assert out.rows == [["a"], ["b"]]


def test_custom_hash_column_name():
    out = transform(
        _t(), Roles("Email", ["Name"], []), scope_key=scope_key, hash_column="pid"
    )
    assert out.headers == ["ID", "pid", "Q1"]


def test_hash_without_scope_key_is_programming_error():
    with pytest.raises(ValueError):
        transform(_t(), Roles("Email", [], []), scope_key=None)


def test_hash_column_name_clash_error():
    t = Table(["Email", "hash"], [["a@example.com", "x"]], [2])
    with pytest.raises(JtkError, match="hash"):
        transform(t, Roles("Email", [], []), scope_key=scope_key)


def test_empty_hash_cell_error_lists_rows():
    t = Table(
        ["Email"],
        [["a@example.com"], [None], ["b@example.com"], ["  "], ["c@example.com"]],
        [2, 3, 4, 5, 6],
    )
    with pytest.raises(JtkError) as e:
        transform(t, Roles("Email", [], []), scope_key=scope_key)
    msg = str(e.value)
    assert "Email" in msg and "3" in msg and "5" in msg
    assert "example.com" not in msg


def test_collision_error(monkeypatch):
    monkeypatch.setattr(crypto, "hash_email", lambda key, email: "0" * 16)
    t = Table(["Email"], [["a@example.com"], ["b@example.com"]], [2, 3])
    with pytest.raises(JtkError) as e:
        transform(t, Roles("Email", [], []), scope_key=scope_key)
    msg = str(e.value)
    assert "2" in msg and "3" in msg
    assert "example.com" not in msg


def test_input_table_unchanged():
    t = _t()
    out = transform(t, Roles("Email", ["Name"], []), scope_key=scope_key)
    out.rows[0][0] = 999
    out.rows.append([0])
    out.headers.append("x")
    out.row_numbers.append(0)
    assert t.headers == ["ID", "Email", "Name", "Q1"]
    assert t.rows[0] == [1, "taro@example.com", "架空 太郎", "a"]
    assert len(t.rows) == 2 and t.row_numbers == [2, 3]
