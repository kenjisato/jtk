import json

import pytest

from conftest import GUARD_COMMAND, hooks, make_project
from jtk.guard import verify_guard

@pytest.fixture
def project_with_guard(tmp_path):
    return make_project(tmp_path / "研究室", hooks(GUARD_COMMAND))


@pytest.fixture
def project_with_noop_hook(tmp_path):
    return make_project(tmp_path / "研究室", hooks("exit 0"))


def test_verify_passes_with_guard_hook(project_with_guard):
    assert verify_guard(project_with_guard / "private/r.xlsx") == []


def test_verify_fails_without_settings(tmp_path):
    root = make_project(tmp_path / "研究室", None)
    assert verify_guard(root / "private/r.xlsx") == ["`.claude/settings.json` も `.claude/settings.local.json` もありません"]


def test_verify_fails_when_hook_allows(project_with_noop_hook):
    failures = verify_guard(project_with_noop_hook / "private/r.xlsx")
    assert failures == ["Read が拒まれません", "Bash が拒まれません", "Grep が拒まれません"]


def test_verify_fails_when_target_not_in_paths(project_with_guard):
    failures = verify_guard(project_with_guard / "public/r.xlsx")
    assert failures
    assert "Read が拒まれません" in failures


def test_verify_fails_when_no_matcher_hits(tmp_path):
    root = make_project(tmp_path / "研究室", hooks(GUARD_COMMAND, matcher="Write|Edit"))
    failures = verify_guard(root / "private/r.xlsx")
    assert len(failures) == 1
    assert "PreToolUse" in failures[0]


def test_verify_merges_local_settings(tmp_path):
    root = make_project(tmp_path / "研究室", {}, local=hooks(GUARD_COMMAND, matcher="Read|Bash|Grep"))
    assert verify_guard(root / "private/r.xlsx") == []


def test_verify_counts_json_deny_on_stdout(tmp_path):
    out = json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny"}})
    root = make_project(tmp_path / "研究室", hooks(f"cat > /dev/null; echo '{out}'"))
    assert verify_guard(root / "private/r.xlsx") == []


def test_verify_reports_unreadable_settings(tmp_path):
    root = make_project(tmp_path / "研究室", None)
    (root / ".claude" / "settings.json").write_text("{not json", encoding="utf-8")
    failures = verify_guard(root / "private/r.xlsx")
    assert len(failures) == 1
    assert "r.xlsx" not in failures[0]


def test_verify_reports_hook_timeout(tmp_path, monkeypatch):
    import jtk.guard

    monkeypatch.setattr(jtk.guard, "HOOK_TIMEOUT", 1)
    root = make_project(tmp_path / "研究室", hooks("sleep 5", matcher="Read"))
    failures = verify_guard(root / "private/r.xlsx")
    assert "Read が拒まれません" in failures
    assert all("r.xlsx" not in f and "研究室" not in f for f in failures)


def test_verify_passes_with_local_settings_only(tmp_path):
    root = make_project(tmp_path / "研究室", None, local=hooks(GUARD_COMMAND))
    assert not (root / ".claude" / "settings.json").exists()
    assert verify_guard(root / "private/r.xlsx") == []
