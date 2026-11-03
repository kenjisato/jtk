import errno
import os
import shutil
from datetime import datetime

import pytest
from openpyxl import load_workbook
from typer.testing import CliRunner

from conftest import FORMS_HEADERS, MASTER_TEXT, _default_forms_rows
from jtk.cli import app
from jtk.crypto import decode_master_key, derive_scope_key, hash_email
from jtk.table.io import read_table

runner = CliRunner()
SCOPE = "2026後期/経済数学I"
SCOPE2 = "2027前期/経済数学I"
TARO = "a4cd517475a2aba2"
TARO2 = "c81c82a06bed50db"


def run(*args):
    return runner.invoke(app, ["table", "pii", *map(str, args)])


def sheet(path):
    wb = load_workbook(path)
    rows = list(wb.active.iter_rows(values_only=True))
    wb.close()
    return [list(r) for r in rows]


def assert_untouched(inp, out, before=None):
    assert not out.exists()
    assert inp.exists()
    if before is not None:
        assert inp.read_bytes() == before
    assert list(out.parent.glob(".*")) == []


def leak_rows(q1_row3):
    # 表計算ソフト上の行 3（見出しの次の次）= データの 2 件目
    rows = _default_forms_rows()
    rows[1][5] = q1_row3
    return rows


@pytest.mark.parametrize("lang", ["en", "ja"])
def test_hash_msforms_xlsx(lang, forms_xlsx, master_key, tmp_path):
    inp = forms_xlsx(lang)
    out = tmp_path / "o.xlsx"
    r = run("hash", inp, "--preset", "msforms", "--scope", SCOPE, "--out", out)
    assert r.exit_code == 0, r.output
    h = FORMS_HEADERS[lang]
    got = sheet(out)
    assert got[0] == [h[0], h[1], h[2], "hash", "Q1"]
    sk = derive_scope_key(decode_master_key(MASTER_TEXT), SCOPE)
    assert [g[3] for g in got[1:]] == [TARO, hash_email(sk, "Hanako@Example.com"), TARO]
    for g, s in zip(got[1:], _default_forms_rows()):
        assert isinstance(g[0], int) and isinstance(g[1], datetime)
        assert [g[0], g[1], g[2], g[4]] == [s[0], s[1], s[2], s[5]]
    assert "312e56e9" in r.output and SCOPE in r.output


@pytest.mark.parametrize("lang", ["en", "ja"])
def test_drop_msforms_xlsx(lang, forms_xlsx, tmp_path):
    inp = forms_xlsx(lang)
    out = tmp_path / "o.xlsx"
    r = run("drop", inp, "--preset", "msforms", "--out", out)
    assert r.exit_code == 0, r.output
    h = FORMS_HEADERS[lang]
    got = sheet(out)
    assert got[0] == [h[0], h[1], h[2], "Q1"]
    for g, s in zip(got[1:], _default_forms_rows()):
        assert g == [s[0], s[1], s[2], s[5]]


def test_drop_works_without_keyring(forms_xlsx, broken_keyring, tmp_path):
    out = tmp_path / "o.xlsx"
    r = run("drop", forms_xlsx(), "--preset", "msforms", "--out", out)
    assert r.exit_code == 0, r.output
    assert "312e56e9" not in r.output and "鍵の指紋" not in r.output
    assert "スコープ" not in r.output


def test_hash_msforms_csv(forms_csv, master_key, tmp_path):
    out = tmp_path / "o.csv"
    r = run("hash", forms_csv(), "--preset", "msforms", "--scope", SCOPE, "--out", out)
    assert r.exit_code == 0, r.output
    assert out.read_bytes()[:3] == b"\xef\xbb\xbf"
    t, _ = read_table(out)
    assert t.headers == ["ID", "Start time", "Completion time", "hash", "Q1"]
    sk = derive_scope_key(decode_master_key(MASTER_TEXT), SCOPE)
    assert [row[3] for row in t.rows] == [TARO, hash_email(sk, "Hanako@Example.com"), TARO]
    for row, src in zip(t.rows, _default_forms_rows()):
        assert [row[0], row[1], row[2], row[4]] == [
            str(src[0]), src[1].isoformat(sep=" "), src[2].isoformat(sep=" "), src[5]
        ]


def test_same_person_across_files_and_scopes(forms_xlsx, master_key, tmp_path):
    a = forms_xlsx(name="a.xlsx")
    rows = _default_forms_rows()
    rows[0][3] = "TARO@example.com "
    b = forms_xlsx(rows=rows, name="b.xlsx")
    oa, ob, oc = (tmp_path / n for n in ("oa.xlsx", "ob.xlsx", "oc.xlsx"))
    for inp, out, scope in ((a, oa, SCOPE), (b, ob, SCOPE), (a, oc, SCOPE2)):
        r = run("hash", inp, "--preset", "msforms", "--scope", scope, "--out", out)
        assert r.exit_code == 0, r.output
    assert sheet(oa)[1][3] == sheet(ob)[1][3] == TARO
    assert sheet(oc)[1][3] == TARO2


@pytest.mark.parametrize("cmd", ["drop", "hash"])
def test_leak_writes_nothing_and_hides_name(cmd, forms_xlsx, master_key, tmp_path):
    inp = forms_xlsx(rows=leak_rows("架空太郎さんと一緒に"))
    out = tmp_path / "o.xlsx"
    extra = ["--scope", SCOPE] if cmd == "hash" else []
    r = run(cmd, inp, "--preset", "msforms", "--out", out, *extra)
    assert r.exit_code == 1
    assert "行 3 / 列 Q1 / " in r.output
    for s in ("架空", "太郎", "taro"):
        assert s not in r.output
    assert_untouched(inp, out)


def test_drop_finds_email_in_free_text(forms_xlsx, tmp_path):
    inp = forms_xlsx(rows=leak_rows("連絡は hanako@example.com まで"))
    out = tmp_path / "o.xlsx"
    r = run("drop", inp, "--preset", "msforms", "--out", out)
    assert r.exit_code == 1
    assert "hanako" not in r.output.lower()
    assert_untouched(inp, out)


def test_hash_without_key_writes_nothing(forms_xlsx, fake_keyring, tmp_path):
    inp = forms_xlsx()
    out = tmp_path / "o.xlsx"
    r = run("hash", inp, "--preset", "msforms", "--scope", SCOPE, "--out", out)
    assert r.exit_code == 1
    assert "jtk key set" in r.output
    assert_untouched(inp, out)


def test_move_original(forms_xlsx, tmp_path):
    inp = forms_xlsx()
    out = tmp_path / "o.xlsx"
    moved = tmp_path / "x" / "y" / "forms.xlsx"
    r = run("drop", inp, "--preset", "msforms", "--out", out, "--move-original", moved)
    assert r.exit_code == 0, r.output
    assert out.exists() and moved.exists() and not inp.exists()
    assert str(moved) in r.output


def _patch_replace(monkeypatch, when, exc):
    real = os.replace

    def fake(src, dst, *a, **k):
        if when(os.fspath(src), os.fspath(dst)):
            raise exc
        return real(src, dst, *a, **k)

    monkeypatch.setattr(os, "replace", fake)


def test_move_failure_removes_output(forms_xlsx, tmp_path, monkeypatch):
    inp = forms_xlsx()
    before = inp.read_bytes()
    out = tmp_path / "o.xlsx"
    moved = tmp_path / "m" / "forms.xlsx"
    _patch_replace(monkeypatch, lambda s, d: s == os.fspath(inp), PermissionError("locked"))
    r = run("drop", inp, "--preset", "msforms", "--out", out, "--move-original", moved)
    assert r.exit_code == 1
    assert "原本を移せませんでした" in r.output
    assert_untouched(inp, out, before)
    assert not moved.exists()
    assert list(moved.parent.glob("*")) == []


def test_move_failure_keeps_existing_target_with_force(forms_xlsx, tmp_path, monkeypatch):
    inp = forms_xlsx()
    before = inp.read_bytes()
    out = tmp_path / "o.xlsx"
    moved = tmp_path / "m" / "forms.xlsx"
    moved.parent.mkdir()
    moved.write_bytes(b"old")
    _patch_replace(monkeypatch, lambda s, d: s == os.fspath(inp), PermissionError("locked"))
    r = run("drop", inp, "--preset", "msforms", "--out", out,
            "--move-original", moved, "--force")
    assert r.exit_code == 1
    assert_untouched(inp, out, before)
    assert moved.read_bytes() == b"old"


def test_move_across_devices_leaves_no_copy_when_delete_fails(forms_xlsx, tmp_path, monkeypatch):
    inp = forms_xlsx()
    before = inp.read_bytes()
    out = tmp_path / "o.xlsx"
    moved = tmp_path / "m" / "forms.xlsx"
    _patch_replace(monkeypatch, lambda s, d: s == os.fspath(inp), OSError(errno.EXDEV, "xdev"))
    real_unlink = type(inp).unlink

    def fake_unlink(self, *a, **k):
        if self == inp:
            raise PermissionError("locked")
        return real_unlink(self, *a, **k)

    monkeypatch.setattr(type(inp), "unlink", fake_unlink)
    r = run("drop", inp, "--preset", "msforms", "--out", out, "--move-original", moved)
    assert r.exit_code == 1
    assert_untouched(inp, out, before)
    assert list(moved.parent.glob("*")) == [] and not moved.exists()


def _xdev_with_failing_final_replace(monkeypatch, inp, moved):
    def when(s, d):
        return s == os.fspath(inp) or (s.endswith(".jtk-tmp") and d == os.fspath(moved))

    def exc_for(s):
        return OSError(errno.EXDEV, "xdev") if s == os.fspath(inp) else PermissionError("x")

    real = os.replace

    def fake(src, dst, *a, **k):
        if when(os.fspath(src), os.fspath(dst)):
            raise exc_for(os.fspath(src))
        return real(src, dst, *a, **k)

    monkeypatch.setattr(os, "replace", fake)


def test_move_across_devices_restores_original_when_final_replace_fails(
    forms_xlsx, tmp_path, monkeypatch
):
    inp = forms_xlsx()
    before = inp.read_bytes()
    out = tmp_path / "o.xlsx"
    moved = tmp_path / "m" / "forms.xlsx"
    _xdev_with_failing_final_replace(monkeypatch, inp, moved)
    r = run("drop", inp, "--preset", "msforms", "--out", out, "--move-original", moved)
    assert r.exit_code == 1
    assert_untouched(inp, out, before)
    assert list(moved.parent.glob("*")) == [] and not moved.exists()


def test_move_across_devices_keeps_temp_when_restore_fails(forms_xlsx, tmp_path, monkeypatch):
    inp = forms_xlsx()
    before = inp.read_bytes()
    out = tmp_path / "o.xlsx"
    moved = tmp_path / "m" / "forms.xlsx"
    _xdev_with_failing_final_replace(monkeypatch, inp, moved)
    real_copy2 = shutil.copy2

    def fake_copy2(src, dst, *a, **k):
        if os.fspath(dst) == os.fspath(inp):
            raise PermissionError("locked")
        return real_copy2(src, dst, *a, **k)

    monkeypatch.setattr(shutil, "copy2", fake_copy2)
    r = run("drop", inp, "--preset", "msforms", "--out", out, "--move-original", moved)
    assert r.exit_code == 1
    kept = list(moved.parent.glob(".*"))
    assert len(kept) == 1 and kept[0].read_bytes() == before
    assert str(kept[0]) in r.output
    assert not out.exists() and not inp.exists()


def test_move_across_devices_succeeds(forms_xlsx, tmp_path, monkeypatch):
    inp = forms_xlsx()
    before = inp.read_bytes()
    out = tmp_path / "o.xlsx"
    moved = tmp_path / "m" / "forms.xlsx"
    _patch_replace(monkeypatch, lambda s, d: s == os.fspath(inp), OSError(errno.EXDEV, "xdev"))
    r = run("drop", inp, "--preset", "msforms", "--out", out, "--move-original", moved)
    assert r.exit_code == 0, r.output
    assert moved.read_bytes() == before and not inp.exists() and out.exists()


def test_final_write_failure_leaves_nothing(forms_xlsx, tmp_path, monkeypatch):
    inp = forms_xlsx()
    before = inp.read_bytes()
    out = tmp_path / "o.xlsx"
    _patch_replace(monkeypatch, lambda s, d: d == os.fspath(out), OSError("disk"))
    r = run("drop", inp, "--preset", "msforms", "--out", out)
    assert r.exit_code == 1
    assert "o.xlsx" in r.output and "jtk-tmp" not in r.output
    assert_untouched(inp, out, before)


def test_existing_out_requires_force(forms_xlsx, tmp_path):
    inp = forms_xlsx()
    out = tmp_path / "o.xlsx"
    out.write_bytes(b"old")
    r = run("drop", inp, "--preset", "msforms", "--out", out)
    assert r.exit_code == 1
    assert out.read_bytes() == b"old"
    r = run("drop", inp, "--preset", "msforms", "--out", out, "--force")
    assert r.exit_code == 0, r.output
    assert sheet(out)[0][-1] == "Q1"


def test_existing_move_target_requires_force(forms_xlsx, tmp_path):
    inp = forms_xlsx()
    out = tmp_path / "o.xlsx"
    moved = tmp_path / "m" / "forms.xlsx"
    moved.parent.mkdir()
    moved.write_bytes(b"old")
    r = run("drop", inp, "--preset", "msforms", "--out", out, "--move-original", moved)
    assert r.exit_code == 1
    assert_untouched(inp, out)
    assert moved.read_bytes() == b"old"


def test_suffix_mismatch_error(forms_xlsx, tmp_path):
    inp = forms_xlsx()
    out = tmp_path / "o.csv"
    r = run("drop", inp, "--preset", "msforms", "--out", out)
    assert r.exit_code == 1
    assert_untouched(inp, out)


def test_suffix_case_insensitive(forms_xlsx, tmp_path):
    out = tmp_path / "o.XLSX"
    r = run("drop", forms_xlsx(), "--preset", "msforms", "--out", out)
    assert r.exit_code == 0, r.output


def test_out_same_as_input_error(forms_xlsx):
    inp = forms_xlsx()
    before = inp.read_bytes()
    r = run("drop", inp, "--preset", "msforms", "--out", inp, "--force")
    assert r.exit_code == 1
    assert inp.read_bytes() == before


def test_move_same_as_out_error(forms_xlsx, tmp_path):
    inp = forms_xlsx()
    out = tmp_path / "o.xlsx"
    r = run("drop", inp, "--preset", "msforms", "--out", out, "--move-original", out)
    assert r.exit_code == 1
    assert_untouched(inp, out)


def test_hash_scope_required(forms_xlsx, master_key, tmp_path):
    inp = forms_xlsx()
    out = tmp_path / "o.xlsx"
    r = run("hash", inp, "--preset", "msforms", "--out", out)
    assert r.exit_code == 1
    assert "scope" in r.output
    assert_untouched(inp, out)


def test_drop_has_no_hash_options(forms_xlsx, tmp_path):
    inp = forms_xlsx()
    out = tmp_path / "o.xlsx"
    for opt in (["--scope", SCOPE], ["--hash", "Email"], ["--hash-column", "h"]):
        r = run("drop", inp, "--preset", "msforms", "--out", out, *opt)
        assert r.exit_code != 0
    assert_untouched(inp, out)


def test_broken_input_hides_content(tmp_path):
    inp = tmp_path / "bad.xlsx"
    inp.write_text("架空 太郎,taro@example.com", encoding="utf-8")
    out = tmp_path / "o.xlsx"
    r = run("drop", inp, "--pii", "Email", "--out", out)
    assert r.exit_code == 1
    assert "太郎" not in r.output and "taro" not in r.output
    assert_untouched(inp, out)


def test_empty_hash_cell_writes_nothing(forms_xlsx, master_key, tmp_path):
    rows = _default_forms_rows()
    rows[1][3] = None
    inp = forms_xlsx(rows=rows)
    out = tmp_path / "o.xlsx"
    r = run("hash", inp, "--preset", "msforms", "--scope", SCOPE, "--out", out)
    assert r.exit_code == 1
    assert "行 3" in r.output
    assert_untouched(inp, out)


# --- 設定ファイル ---------------------------------------------------------

CONFIG = """
[jtk.table.pii]
preset = "msforms"
scope = "2026後期/経済数学I"
out_dir = "analysis/forms"
out_name = "{stem}_pii"
move_original_dir = "private/forms"
"""


@pytest.fixture
def cfg(tmp_path, forms_xlsx):
    course = tmp_path / "course"
    course.mkdir()
    path = course / "jtk.toml"
    path.write_text(CONFIG, encoding="utf-8")
    inp = forms_xlsx(name="forms.xlsx", directory=tmp_path / "inbox")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    return path, inp, course, elsewhere


def test_config_only_hash(cfg, master_key, monkeypatch):
    path, inp, course, elsewhere = cfg
    monkeypatch.chdir(elsewhere)
    r = run("hash", inp, "--config", path)
    assert r.exit_code == 0, r.output
    out = course / "analysis" / "forms" / "forms_pii.xlsx"
    assert sheet(out)[1][3] == TARO
    assert not inp.exists()
    assert (course / "private" / "forms" / "forms.xlsx").exists()
    assert str(path) in r.output


def test_config_only_drop_ignores_scope(cfg, broken_keyring, monkeypatch):
    path, inp, course, elsewhere = cfg
    monkeypatch.chdir(elsewhere)
    r = run("drop", inp, "--config", path)
    assert r.exit_code == 0, r.output
    assert (course / "analysis" / "forms" / "forms_pii.xlsx").exists()


def test_cli_overrides_config(cfg, master_key, tmp_path):
    path, inp, course, _ = cfg
    out = tmp_path / "mine.xlsx"
    r = run("hash", inp, "--config", path, "--out", out, "--scope", SCOPE2)
    assert r.exit_code == 0, r.output
    assert sheet(out)[1][3] == TARO2


def test_cli_pii_omit_add_to_config(forms_xlsx, tmp_path):
    cfg_path = tmp_path / "c.toml"
    cfg_path.write_text('[jtk.table.pii]\npreset = "msforms"\nomit = ["ID"]\n', encoding="utf-8")
    inp = forms_xlsx()
    out = tmp_path / "o.xlsx"
    r = run("drop", inp, "--config", cfg_path, "--omit", "Q1", "--out", out)
    assert r.exit_code == 0, r.output
    assert sheet(out)[0] == ["Start time", "Completion time"]


def test_config_unknown_key_writes_nothing(forms_xlsx, tmp_path):
    cfg_path = tmp_path / "c.toml"
    cfg_path.write_text('[jtk.table.pii]\nbogus_key = "x"\n', encoding="utf-8")
    inp = forms_xlsx()
    out = tmp_path / "o.xlsx"
    r = run("drop", inp, "--config", cfg_path, "--out", out)
    assert r.exit_code == 1
    assert "bogus_key" in r.output
    assert_untouched(inp, out)


def test_out_required_without_config(forms_xlsx):
    r = run("drop", forms_xlsx(), "--preset", "msforms")
    assert r.exit_code == 1
    assert "out_dir" in r.output
