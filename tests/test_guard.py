import json

import pytest
from typer.testing import CliRunner

from jtk.cli import app
from jtk.errors import JtkError
from jtk.guard import GuardConfig, decide, glob_match, load_guard_config

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
    assert reason == "jtk guard: 守られたファイルです（private/student-ids-PC1.xlsx）"


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
    assert reason == "jtk guard: 守られたファイルです（PRIVATE/student-ids.xlsx）"


def test_cli_exit_code_2_on_deny(cli_runner, cfg):
    r = run_guard(cli_runner, {
        "cwd": str(cfg.root), "hook_event_name": "PreToolUse", "tool_name": "Read",
        "tool_input": {"file_path": str(cfg.root / "private" / "student-ids.xlsx")},
    })
    assert r.exit_code == 2
    assert "jtk guard: 守られたファイルです（private/student-ids.xlsx）" in r.stderr


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
    assert reason == "jtk guard: 守られたファイルです（B25/abcd/sub/計画.DOCX）"


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
    assert reason == "jtk guard: 守られたファイルです（private/x）"


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
    assert call("Grep", cfg, pattern="abc") is None


def test_guard_grep_allowed_on_other_dir(cfg):
    assert call("Grep", cfg, pattern="abc", path=str(cfg.root / "B25")) is None
    assert call("Glob", cfg, pattern="**/*.txt", path=str(cfg.root / "B25")) is None


def test_guard_denies_grep_pattern_and_glob_words(cfg):
    assert call("Glob", cfg, pattern="private/**") == "jtk guard: 守られたファイルです（private/）"
    assert call("Glob", cfg, pattern="**/*.PDF") == "jtk guard: 守られたファイルです（.pdf）"
    assert call("Grep", cfg, pattern="abc", glob="*.docx") == "jtk guard: 守られたファイルです（.docx）"


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
    assert "private/名簿.xlsx" in r.stderr
