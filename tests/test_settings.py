import unicodedata

import pytest

from jtk.errors import JtkError
from jtk.table.settings import (
    PiiSettings,
    load_pii_settings,
    moved_original_path,
    output_path,
)

FULL = r"""
[jtk.table.pii]
preset = "msforms"
scope = "2026後期/経済数学I"        # pii hash だけが読む
hash_column = "hash"                 # pii hash だけが読む
pii = ["学籍番号"]
omit = ["ID", "開始時刻", "完了時刻"]
out_dir = "analysis/forms"
out_name = "{stem}"                  # 拡張子は入力と同じものが付く
stem_pattern = '^(\d+-\d+) .*$'      # 任意。{stem} に入れる前にファイル名を書き換える
stem_replace = '\1'
move_original_dir = "private/forms"
"""


def write(tmp_path, body, name="jtk.toml"):
    d = tmp_path / "course"
    d.mkdir(exist_ok=True)
    p = d / name
    p.write_text(body, encoding="utf-8")
    return p


def pii_cfg(tmp_path, body):
    return write(tmp_path, "[jtk.table.pii]\n" + body)


def test_full_settings(tmp_path):
    cfg = write(tmp_path, FULL)
    s = load_pii_settings(cfg)
    assert s.preset == "msforms"
    assert s.scope == "2026後期/経済数学I" and s.omit == ["ID", "開始時刻", "完了時刻"]
    assert s.hash_column == "hash" and s.pii == ["学籍番号"]
    assert s.out_dir == (tmp_path / "course" / "analysis" / "forms").resolve()
    assert s.move_original_dir == (tmp_path / "course" / "private" / "forms").resolve()
    assert s.stem_pattern.pattern == r"^(\d+-\d+) .*$" and s.stem_replace == r"\1"
    assert s.source == cfg


def test_relative_paths_do_not_depend_on_cwd(tmp_path, monkeypatch):
    cfg = pii_cfg(tmp_path, 'out_dir = "o"\n')
    a = load_pii_settings(cfg).out_dir
    other = tmp_path / "elsewhere"
    other.mkdir()
    monkeypatch.chdir(other)
    assert load_pii_settings(cfg).out_dir == a


def test_absolute_out_dir_kept(tmp_path):
    target = (tmp_path / "abs").resolve()
    cfg = pii_cfg(tmp_path, f"out_dir = '{target}'\n")
    assert load_pii_settings(cfg).out_dir == target


def test_missing_table_gives_defaults(tmp_path):
    cfg = write(tmp_path, "[other]\nx = 1\n")
    s = load_pii_settings(cfg)
    assert s == PiiSettings(source=cfg)
    assert s.out_name == "{stem}"


def test_other_tables_ignored(tmp_path):
    cfg = write(tmp_path, "[jtk.table.other]\nfoo = 1\n")
    assert load_pii_settings(cfg).source == cfg


def test_unknown_key_error(tmp_path):
    with pytest.raises(JtkError, match="out_dri"):
        load_pii_settings(pii_cfg(tmp_path, 'out_dri = "x"\n'))


def test_wrong_type_error(tmp_path):
    with pytest.raises(JtkError, match="pii"):
        load_pii_settings(pii_cfg(tmp_path, 'pii = "Name"\n'))


def test_missing_file_error(tmp_path):
    with pytest.raises(JtkError):
        load_pii_settings(tmp_path / "none.toml")


def test_broken_toml_error(tmp_path):
    with pytest.raises(JtkError):
        load_pii_settings(write(tmp_path, "[jtk\n"))


def test_non_table_error(tmp_path):
    with pytest.raises(JtkError, match="jtk"):
        load_pii_settings(write(tmp_path, "jtk = 1\n"))


def settings_with(tmp_path, body):
    return load_pii_settings(pii_cfg(tmp_path, f'out_dir = "out"\n{body}'))


def test_output_path(tmp_path):
    s = settings_with(tmp_path, 'out_name = "{stem}_pii"\n')
    got = output_path(s, tmp_path / "第3回 小テスト.xlsx")
    assert got == s.out_dir / "第3回 小テスト_pii.xlsx"


def test_output_path_without_out_dir_error(tmp_path):
    s = load_pii_settings(pii_cfg(tmp_path, ""))
    with pytest.raises(JtkError):
        output_path(s, tmp_path / "a.xlsx")


@pytest.mark.parametrize("bad", ["{date}", "a/{stem}", "a\\{stem}", "{stem}.xlsx", "{stem}.CSV", "{stem"])
def test_bad_out_name_error(bad, tmp_path):
    with pytest.raises(JtkError, match="out_name"):
        load_pii_settings(pii_cfg(tmp_path, f"out_name = '{bad}'\n"))


def test_stem_rewrite_session_quiz(tmp_path):
    s = settings_with(tmp_path, "stem_pattern = '^(\\d+-\\d+) .*$'\nstem_replace = '\\1'\n")
    got = output_path(s, tmp_path / "03-2 確率の復習(1-100).xlsx")
    assert got == s.out_dir / "03-2.xlsx"


def test_stem_rewrite_forms_suffix(tmp_path):
    s = settings_with(
        tmp_path,
        "stem_pattern = '^(.*)\\(\\d+-\\d+\\)$'\nstem_replace = '\\1'\nout_name = '{stem}_pii'\n",
    )
    got = output_path(s, tmp_path / "確率の復習(1-100).csv")
    assert got == s.out_dir / "確率の復習_pii.csv"


def test_stem_rewrite_nfd_filename(tmp_path):
    s = settings_with(tmp_path, "stem_pattern = '^(.*)データ$'\nstem_replace = '\\1'\n")
    name = unicodedata.normalize("NFD", "演習データ") + ".xlsx"
    assert output_path(s, tmp_path / name) == s.out_dir / "演習.xlsx"


def test_stem_no_match_error(tmp_path):
    s = settings_with(tmp_path, "stem_pattern = '^(\\d+-\\d+) '\nstem_replace = '\\1'\n")
    with pytest.raises(JtkError, match="stem_pattern"):
        output_path(s, tmp_path / "確率の復習.xlsx")


def test_stem_empty_result_error(tmp_path):
    s = settings_with(tmp_path, "stem_pattern = '^.*$'\nstem_replace = ''\n")
    with pytest.raises(JtkError):
        output_path(s, tmp_path / "a.xlsx")


def test_stem_pattern_without_replace_error(tmp_path):
    with pytest.raises(JtkError, match="stem_replace"):
        load_pii_settings(pii_cfg(tmp_path, "stem_pattern = 'a'\n"))


def test_stem_bad_regex_error(tmp_path):
    with pytest.raises(JtkError, match="stem_pattern"):
        load_pii_settings(pii_cfg(tmp_path, "stem_pattern = '('\nstem_replace = 'x'\n"))


def test_stem_bad_backref_error(tmp_path):
    s = settings_with(tmp_path, "stem_pattern = '^(.*)$'\nstem_replace = '\\2'\n")
    with pytest.raises(JtkError, match="stem_replace"):
        output_path(s, tmp_path / "a.xlsx")


def test_moved_original_path(tmp_path):
    cfg = pii_cfg(tmp_path, 'move_original_dir = "priv"\n')
    s = load_pii_settings(cfg)
    assert moved_original_path(s, tmp_path / "x" / "第3回 小テスト.xlsx") == s.move_original_dir / "第3回 小テスト.xlsx"
    assert moved_original_path(load_pii_settings(pii_cfg(tmp_path, "")), tmp_path / "a.xlsx") is None
