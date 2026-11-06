import json
import os
import re
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from jtk.cli import app
from jtk.errors import JtkError
import jtk.guard
from jtk.guard import SEARCH_ROOT_REASON, GuardConfig, decide, glob_match, load_guard_config

CONFIG_TOML = """\
[jtk.guard]
paths = ["private/**", "_inbox/**", "B25/**/*.docx", "B25/**/*.pdf", "M25/**/*.docx", "M25/**/*.pdf"]
command_words = ["student-ids", "_inbox", "private/", "private\\\\", ".docx", ".pdf", "--danger"]
"""


@pytest.fixture
def cfg(tmp_path):
    root = tmp_path / "研究室"
    (root / ".claude").mkdir(parents=True)
    (root / ".claude" / "jtk-guard.toml").write_text(CONFIG_TOML, encoding="utf-8")
    config = load_guard_config(root)
    assert config is not None
    return config


@pytest.fixture
def cli_runner():
    return CliRunner()


def call(tool, cfg, cwd=None, **tool_input):
    payload = {
        "session_id": "s",
        "cwd": str(cwd if cwd is not None else cfg.root),
        "hook_event_name": "PreToolUse",
        "tool_name": tool,
        "tool_input": tool_input,
    }
    return decide(payload, cfg)


def run_guard(cli_runner, payload):
    data = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return cli_runner.invoke(app, ["guard"], input=data)


# --- brief tests ---


def test_guard_denies_read_of_roster(cfg):
    assert decide({"tool_name": "Read", "tool_input": {"file_path": str(cfg.root / "private/student-ids.xlsx")}}, cfg)


def test_guard_denies_any_file_under_protected_dir(cfg):
    reason = call("Read", cfg, file_path=str(cfg.root / "private" / "student-ids-PC1.xlsx"))
    assert reason == "jtk guard: 守られたファイルです（private）"


def test_guard_denies_grep_on_protected_dir(cfg):
    reason = call("Grep", cfg, pattern="abc", path=str(cfg.root / "private"))
    assert reason == "jtk guard: 守られたファイルです（private）"


def test_guard_allows_txt_in_student_dir(cfg):
    assert call("Read", cfg, file_path=str(cfg.root / "B25" / "abcd" / "2026-09_研究計画書.txt")) is None


def test_guard_denies_bash_cat(cfg):
    reason = call("Bash", cfg, command="cat private/student-ids.xlsx")
    assert reason == "jtk guard: 守られたファイルです（student-ids）"


def test_guard_denies_danger_flag(cfg):
    reason = call("Bash", cfg, command="jtk table roster fill x --danger")
    assert reason == "jtk guard: 守られたファイルです（--danger）"


def test_guard_windows_paths_case_insensitive(cfg):
    win = str(cfg.root).replace("/", "\\") + "\\PRIVATE\\student-ids.xlsx"
    reason = call("Read", cfg, file_path=win)
    assert reason == "jtk guard: 守られたファイルです（private）"


def test_cli_exit_code_2_on_deny(cli_runner, cfg):
    r = run_guard(cli_runner, {
        "cwd": str(cfg.root), "hook_event_name": "PreToolUse", "tool_name": "Read",
        "tool_input": {"file_path": str(cfg.root / "private" / "student-ids.xlsx")},
    })
    assert r.exit_code == 2
    assert "jtk guard: 守られたファイルです（private）" in r.stderr


def test_cli_exit_0_without_config(cli_runner, tmp_path):
    r = run_guard(cli_runner, {
        "cwd": str(tmp_path), "hook_event_name": "PreToolUse", "tool_name": "Read",
        "tool_input": {"file_path": str(tmp_path / "private" / "student-ids.xlsx")},
    })
    assert r.exit_code == 0
    assert r.stderr == ""


# --- paths ---


def test_guard_denies_docx_deep_in_student_dir(cfg):
    reason = call("Edit", cfg, file_path=str(cfg.root / "B25" / "abcd" / "sub" / "計画.DOCX"))
    assert reason == "jtk guard: 守られたファイルです（B25/**/*.docx）"


def test_guard_denies_pdf_directly_in_cohort_dir(cfg):
    assert call("Write", cfg, file_path=str(cfg.root / "M25" / "x.pdf"))


def test_guard_denies_notebook_and_multiedit(cfg):
    assert call("NotebookEdit", cfg, notebook_path=str(cfg.root / "_inbox" / "a.ipynb"))
    assert call("MultiEdit", cfg, file_path=str(cfg.root / "private" / "a.txt"))


def test_guard_relative_path_resolved_against_cwd(cfg):
    assert call("Read", cfg, cwd=cfg.root / "B25", file_path="../private/student-ids.xlsx")
    assert call("Read", cfg, file_path="private/student-ids.xlsx")
    assert call("Read", cfg, cwd=cfg.root / "B25", file_path="abcd/x.txt") is None


def test_guard_dotdot_is_collapsed(cfg):
    reason = call("Read", cfg, file_path=str(cfg.root / "B25" / ".." / "private" / "x"))
    assert reason == "jtk guard: 守られたファイルです（private）"


def test_guard_forward_slash_windows_path(cfg):
    p = str(cfg.root).replace("\\", "/") + "/private/x.xlsx"
    assert call("Read", cfg, file_path=p)


def test_guard_paths_outside_root_never_match(cfg, tmp_path):
    assert call("Read", cfg, file_path=str(tmp_path / "private" / "student-ids.xlsx")) is None
    assert call("Read", cfg, file_path="../private/x.xlsx") is None


def test_guard_protected_dir_itself_is_not_a_file_match(cfg):
    # Read の対象としての "private" そのものは paths に当たらない（"X/**" は子孫だけ）
    assert call("Read", cfg, file_path=str(cfg.root / "private")) is None


def test_guard_similar_name_is_allowed(cfg):
    assert call("Read", cfg, file_path=str(cfg.root / "private2" / "x.txt")) is None
    assert call("Read", cfg, file_path=str(cfg.root / "notes" / "private" / "x.txt")) is None


# --- Grep / Glob ---


def test_guard_denies_grep_inside_protected_dir(cfg):
    assert call("Grep", cfg, pattern="x", path=str(cfg.root / "private" / "sub"))
    assert call("Glob", cfg, pattern="*.xlsx", path="PRIVATE")


def test_guard_grep_without_path_uses_cwd(cfg):
    assert call("Grep", cfg, cwd=cfg.root / "private", pattern="abc") == "jtk guard: 守られたファイルです（private）"
    assert call("Glob", cfg, cwd=cfg.root / "_inbox", pattern="*") is not None
    assert call("Grep", cfg, pattern="abc") == SEARCH_ROOT_REASON


def test_guard_grep_allowed_on_other_dir(cfg):
    assert call("Grep", cfg, pattern="abc", path=str(cfg.root / "B25")) is None
    assert call("Glob", cfg, pattern="**/*.txt", path=str(cfg.root / "B25")) is None


def test_guard_denies_grep_pattern_and_glob_words(cfg):
    assert call("Glob", cfg, pattern="private/**", path=str(cfg.root / "B25")) == "jtk guard: 守られたファイルです（private/）"
    assert call("Glob", cfg, pattern="**/*.PDF", path=str(cfg.root / "B25")) == "jtk guard: 守られたファイルです（.pdf）"
    assert call("Grep", cfg, pattern="abc", glob="*.docx", path=str(cfg.root / "B25")) == "jtk guard: 守られたファイルです（.docx）"


# --- Bash / PowerShell ---


def test_guard_command_words_case_insensitive(cfg):
    assert call("PowerShell", cfg, command="Get-Content PRIVATE\\Student-IDs.xlsx")
    assert call("Bash", cfg, command="ls _INBOX") == "jtk guard: 守られたファイルです（_inbox）"


def test_guard_allows_harmless_command(cfg):
    assert call("Bash", cfg, command="uv run pytest -q") is None


def test_guard_unknown_tool_is_allowed(cfg):
    assert call("WebFetch", cfg, url="https://example.com/private/x.pdf") is None


def test_guard_missing_fields_are_allowed(cfg):
    assert call("Read", cfg) is None
    assert call("Bash", cfg) is None
    assert decide({}, cfg) is None


# --- glob matcher ---


@pytest.mark.parametrize("pattern,path,expected", [
    ("private/**", "private/a", True),
    ("private/**", "private/a/b", True),
    ("private/**", "private", False),
    ("private/**", "privatex/a", False),
    ("B25/**/*.docx", "B25/a.docx", True),
    ("B25/**/*.docx", "B25/a/b/c.docx", True),
    ("B25/**/*.docx", "B25/a/b/c.txt", False),
    ("B25/**/*.docx", "B25x/a.docx", False),
    ("**/*.pdf", "a.pdf", True),
    ("**/*.pdf", "a/b.pdf", True),
    ("a/?.txt", "a/b.txt", True),
    ("a/?.txt", "a/bc.txt", False),
    ("a/*.txt", "a/b/c.txt", False),
    ("a/[x].txt", "a/[x].txt", True),
    ("A/*.TXT", "a/b.txt", True),
])
def test_glob_match(pattern, path, expected):
    assert glob_match(pattern, path) is expected


# --- config loading ---


def test_load_config_walks_up_parents(cfg):
    sub = cfg.root / "B25" / "abcd"
    sub.mkdir(parents=True)
    found = load_guard_config(sub)
    assert found == cfg
    assert isinstance(found, GuardConfig)
    assert found.paths[0] == "private/**"
    assert "private\\" in found.command_words


def test_load_config_none_when_absent(tmp_path):
    assert load_guard_config(tmp_path) is None


@pytest.mark.parametrize("text", [
    "[jtk.guard\npaths = [",
    "[other]\nx = 1\n",
    "[jtk]\nguard = 1\n",
    '[jtk.guard]\npaths = "private/**"\ncommand_words = []\n',
    '[jtk.guard]\npaths = [1]\ncommand_words = []\n',
    '[jtk.guard]\ncommand_words = []\n',
    '[jtk.guard]\npaths = []\n',
])
def test_load_config_malformed_raises(tmp_path, text):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "jtk-guard.toml").write_text(text, encoding="utf-8")
    with pytest.raises(JtkError):
        load_guard_config(tmp_path)


# --- CLI fail-closed and encoding ---


def test_cli_exit_0_on_allow(cli_runner, cfg):
    r = run_guard(cli_runner, {
        "cwd": str(cfg.root), "tool_name": "Read",
        "tool_input": {"file_path": str(cfg.root / "B25" / "abcd" / "2026-09_研究計画書.txt")},
    })
    assert r.exit_code == 0
    assert r.stderr == ""


def test_cli_invalid_json_exits_2(cli_runner, cfg):
    r = run_guard(cli_runner, b"{not json")
    assert r.exit_code == 2
    assert "jtk guard" in r.stderr
    assert "not json" not in r.stderr


def test_cli_non_object_json_exits_2(cli_runner):
    r = run_guard(cli_runner, b"[1, 2]")
    assert r.exit_code == 2


def test_cli_malformed_config_exits_2(cli_runner, tmp_path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "jtk-guard.toml").write_text(
        '[jtk.guard]\npaths = "secret-value"\ncommand_words = []\n', encoding="utf-8")
    r = run_guard(cli_runner, {"cwd": str(tmp_path), "tool_name": "Bash", "tool_input": {"command": "ls"}})
    assert r.exit_code == 2
    assert "jtk guard" in r.stderr
    assert "secret-value" not in r.stderr


def test_cli_reads_utf8_japanese_path(cli_runner, cfg):
    payload = {
        "cwd": str(cfg.root), "tool_name": "Read",
        "tool_input": {"file_path": str(cfg.root / "private" / "名簿.xlsx")},
    }
    r = run_guard(cli_runner, json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    assert r.exit_code == 2
    assert "（private）" in r.stderr and "名簿" not in r.stderr


# --- fix round 1 (R11, R12, reason without names) ---


def test_guard_search_from_root_with_explicit_path_is_denied(cfg):
    assert call("Grep", cfg, pattern="abc", path=str(cfg.root)) == SEARCH_ROOT_REASON
    assert call("Glob", cfg, pattern="*.txt", path=".") == SEARCH_ROOT_REASON
    assert call("Glob", cfg, cwd=cfg.root / "B25", pattern="*.txt", path="..") == SEARCH_ROOT_REASON


def test_guard_search_root_reason_has_no_names(cfg):
    assert "private" not in SEARCH_ROOT_REASON and "_inbox" not in SEARCH_ROOT_REASON
    assert "path" in SEARCH_ROOT_REASON


def test_guard_search_from_ancestor_of_root_is_denied(cfg):
    # root の祖先から検索すると、守られたフォルダの中も検索される
    assert call("Grep", cfg, pattern="abc", path=str(cfg.root.parent)) == SEARCH_ROOT_REASON
    assert call("Glob", cfg, pattern="*", path=str(cfg.root.parent.parent)) == SEARCH_ROOT_REASON


def test_guard_search_from_dotdot_is_denied(cfg):
    assert call("Grep", cfg, pattern="abc", path="..") == SEARCH_ROOT_REASON
    assert call("Glob", cfg, cwd=cfg.root / "B25", pattern="*", path="../..") == SEARCH_ROOT_REASON
    assert call("Grep", cfg, cwd=cfg.root.parent, pattern="abc") == SEARCH_ROOT_REASON


def test_guard_search_from_absolute_parent_any_case_is_denied(cfg):
    parent = str(cfg.root.parent)
    assert call("Grep", cfg, pattern="abc", path=parent.upper()) == SEARCH_ROOT_REASON
    assert call("Grep", cfg, pattern="abc", path=parent.replace("\\", "/").lower()) == SEARCH_ROOT_REASON


def test_guard_search_ancestor_without_dir_entry_is_allowed(tmp_path):
    config = GuardConfig(root=tmp_path / "root", paths=["*.docx"], command_words=[])
    assert decide({"cwd": str(tmp_path), "tool_name": "Grep",
                   "tool_input": {"pattern": "x", "path": str(tmp_path)}}, config) is None


def test_guard_search_in_unrelated_dir_outside_root_is_allowed(cfg, tmp_path):
    # root の親の下でも、root の祖先でなければ（兄弟のフォルダ）通す
    sibling = cfg.root.parent / "別のフォルダ"
    assert call("Grep", cfg, pattern="abc", path=str(sibling)) is None
    assert call("Glob", cfg, pattern="*", path="../別のフォルダ") is None
    assert call("Grep", cfg, pattern="abc", path=str(cfg.root.parent / "研究室2")) is None


def test_guard_search_in_sibling_dir_is_allowed(cfg):
    assert call("Grep", cfg, pattern="abc", path=str(cfg.root / "B25")) is None
    assert call("Glob", cfg, cwd=cfg.root / "B25", pattern="*.txt") is None


def test_guard_search_ancestor_of_nested_protected_dir_is_denied(tmp_path):
    config = GuardConfig(root=tmp_path, paths=["a/b/secret/**"], command_words=[])
    assert decide({"cwd": str(tmp_path), "tool_name": "Grep",
                   "tool_input": {"pattern": "x", "path": str(tmp_path / "a")}}, config) == SEARCH_ROOT_REASON
    assert decide({"cwd": str(tmp_path), "tool_name": "Grep",
                   "tool_input": {"pattern": "x", "path": str(tmp_path / "a" / "c")}}, config) is None


def test_guard_reason_omits_name_under_whole_folder_entry(cfg):
    reason = call("Read", cfg, file_path=str(cfg.root / "_inbox" / "架空太郎_計画書.docx"))
    assert reason == "jtk guard: 守られたファイルです（_inbox）"
    assert "架空太郎" not in reason
    reason = call("Glob", cfg, pattern="*", path=str(cfg.root / "_inbox" / "架空花子"))
    assert reason == "jtk guard: 守られたファイルです（_inbox）"


def test_guard_reason_reports_pattern_not_path(cfg):
    reason = call("Read", cfg, file_path=str(cfg.root / "B25" / "abcd" / "架空太郎.pdf"))
    assert reason == "jtk guard: 守られたファイルです（B25/**/*.pdf）"


@pytest.mark.parametrize("key,value", [
    ("pattern", "../*"),
    ("pattern", "B25/../../x/*"),
    ("pattern", r"..\*"),
    ("pattern", "/etc/*"),
    ("pattern", "C:/Users/*"),
    ("pattern", r"c:\x\*"),
    ("glob", "../**"),
])
def test_guard_search_pattern_escaping_root_is_denied(cfg, key, value):
    tool = "Glob" if key == "pattern" else "Grep"
    tool_input = {key: value, "path": str(cfg.root / "B25")}
    if key == "glob":
        tool_input["pattern"] = "abc"
    reason = call(tool, cfg, **tool_input)
    assert reason is not None and "jtk guard" in reason


def test_guard_search_pattern_with_dots_in_name_is_allowed(cfg):
    assert call("Glob", cfg, pattern="**/a..b.txt", path=str(cfg.root / "B25")) is None


def test_cli_unexpected_error_fails_closed(cli_runner, cfg, monkeypatch):
    def boom(payload, config):
        raise RuntimeError("架空太郎")

    monkeypatch.setattr(jtk.guard, "decide", boom)
    r = run_guard(cli_runner, {"cwd": str(cfg.root), "tool_name": "Bash", "tool_input": {"command": "ls"}})
    assert r.exit_code == 2
    assert r.stderr == "jtk guard: 判定できません\n"


# --- final review fixes ---


@pytest.mark.parametrize("given,expected", [
    ("/c/Users/x", ["C:", "Users", "x"]),
    ("/mnt/d/a/b", ["D:", "a", "b"]),
    ("/cygdrive/e/a", ["E:", "a"]),
    ("/c", ["C:"]),
    ("/mnt/c", ["C:"]),
    ("/usr/bin", ["usr", "bin"]),
    ("/mnt", ["mnt"]),
])
def test_segments_maps_posix_drive_prefixes_on_windows(given, expected):
    assert jtk.guard._segments(given, "", windows=True) == expected


def test_segments_keeps_posix_paths_off_windows():
    assert jtk.guard._segments("/c/Users/x", "", windows=False) == ["c", "Users", "x"]


@pytest.mark.skipif(os.name != "nt", reason="Windows のドライブ表記")
@pytest.mark.parametrize("prefix", ["/{d}/", "/mnt/{d}/", "/cygdrive/{d}/"])
def test_guard_denies_git_bash_style_path(cfg, prefix):
    drive, rest = str(cfg.root).replace("\\", "/").split(":/", 1)
    path = prefix.format(d=drive.lower()) + rest + "/private/student-ids.xlsx"
    assert call("Read", cfg, file_path=path) == "jtk guard: 守られたファイルです（private）"


def _readme_guard_toml() -> str:
    text = (Path(__file__).parent.parent / "README.md").read_text(encoding="utf-8")
    block = re.search(r"```toml\n(\[jtk\.guard\].*?)```", text, re.S)
    assert block is not None
    return block.group(1)


@pytest.fixture
def readme_cfg(tmp_path):
    root = tmp_path / "研究室"
    (root / ".claude").mkdir(parents=True)
    (root / ".claude" / "jtk-guard.toml").write_text(_readme_guard_toml(), encoding="utf-8")
    config = load_guard_config(root)
    assert config is not None
    return config


@pytest.mark.parametrize("command", [
    "ls _inbox",
    "cd private && ls",
    "jtk table roster fill x --danger",
    "cat private/student-ids.xlsx",
    "Get-ChildItem _INBOX",
])
def test_readme_config_denies_commands(readme_cfg, command):
    assert call("Bash", readme_cfg, command=command) is not None


def test_readme_config_allows_harmless_command(readme_cfg):
    assert call("Bash", readme_cfg, command="git status") is None


def _readme_hook_command() -> str:
    text = (Path(__file__).parent.parent / "README.md").read_text(encoding="utf-8")
    block = re.search(r'```json\n(\{\s*"hooks".*?)```', text, re.S)
    assert block is not None
    return json.loads(block.group(1))["hooks"]["PreToolUse"][0]["hooks"][0]["command"]


@pytest.mark.skipif(jtk.guard._hook_shell() is None, reason="フックを動かすシェルがない")
@pytest.mark.parametrize("fake_exit,expected", [(1, 2), (2, 2), (0, 0), (127, 2)])
def test_readme_hook_command_fails_closed(tmp_path, fake_exit, expected):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake = bindir / "jtk"
    fake.write_bytes(f"#!/bin/sh\nexit {fake_exit}\n".encode())
    fake.chmod(0o755)
    shell = jtk.guard._hook_shell()
    command = f'PATH="$(cd "{bindir.as_posix()}" && pwd):$PATH"; ' + _readme_hook_command()
    code = subprocess.run([shell, "-c", command], input=b"{}", capture_output=True, timeout=30).returncode
    assert code == expected


@pytest.mark.skipif(jtk.guard._hook_shell() is None, reason="フックを動かすシェルがない")
def test_readme_hook_command_without_jtk_exits_2(tmp_path):
    shell = jtk.guard._hook_shell()
    command = 'command() { return 1; }; ' + _readme_hook_command()
    code = subprocess.run([shell, "-c", command], input=b"{}", capture_output=True, timeout=30).returncode
    assert code == 2


def test_segments_maps_posix_drive_in_cwd_on_windows():
    assert jtk.guard._segments("a/b", "/c/root", windows=True) == ["C:", "root", "a", "b"]
