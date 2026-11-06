"""Claude Code の PreToolUse フック。守られたファイルに触る道具の呼び出しを拒む。

理由の文には root からの相対パスか、設定にある語だけを入れる。ファイルの中身や
コマンドの全文は入れない。
"""

import os
import posixpath
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from jtk.errors import JtkError

CONFIG_RELPATH = Path(".claude") / "jtk-guard.toml"
REASON = "jtk guard: 守られたファイルです（{}）"

FILE_TOOLS = {
    "Read": "file_path",
    "Edit": "file_path",
    "Write": "file_path",
    "MultiEdit": "file_path",
    "NotebookEdit": "notebook_path",
}
SEARCH_TOOLS = {"Grep", "Glob"}
SHELL_TOOLS = {"Bash", "PowerShell"}


@dataclass
class GuardConfig:
    root: Path
    paths: list[str]
    command_words: list[str]


def load_guard_config(start: Path) -> GuardConfig | None:
    """start から親へたどって .claude/jtk-guard.toml を探す。見つからなければ None。

    見つかったのに形が崩れていれば JtkError（呼び出し側は拒む側に倒す）。
    """
    start = Path(os.path.abspath(start))
    for directory in (start, *start.parents):
        candidate = directory / CONFIG_RELPATH
        if candidate.is_file():
            return _parse_config(candidate, directory)
    return None


def _parse_config(path: Path, root: Path) -> GuardConfig:
    malformed = JtkError("jtk guard: 設定ファイル .claude/jtk-guard.toml の形が正しくありません")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        raise malformed from None
    section = data.get("jtk")
    section = section.get("guard") if isinstance(section, dict) else None
    if not isinstance(section, dict):
        raise malformed
    lists = {}
    for key in ("paths", "command_words"):
        value = section.get(key)
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise malformed
        lists[key] = value
    return GuardConfig(root=root, paths=lists["paths"], command_words=lists["command_words"])


def glob_match(pattern: str, path: str) -> bool:
    """`/` 区切りの相対パスを glob で照合する。大文字小文字は区別しない。

    `*` と `?` は区切りをまたがない。`**` の区切りは 0 個以上のディレクトリに当たり、
    末尾の `**` は 1 個以上の子孫に当たる（`X/**` は `X` 自身には当たらない）。
    """
    return re.fullmatch(_glob_regex(pattern.casefold()), path.casefold()) is not None


def _glob_regex(pattern: str) -> str:
    segments = pattern.split("/")
    parts = []
    for i, seg in enumerate(segments):
        last = i == len(segments) - 1
        if seg == "**":
            parts.append(".+" if last else "(?:[^/]+/)*")
            continue
        body = "".join(
            "[^/]*" if ch == "*" else "[^/]" if ch == "?" else re.escape(ch) for ch in seg
        )
        parts.append(body if last else body + "/")
    return "".join(parts)


def _segments(path: str, cwd: str) -> list[str]:
    """パスを `/` 区切りの絶対パスの区切りの列にする。存在は問わない。"""
    s = path.replace("\\", "/")
    if not (s.startswith("/") or re.match(r"[A-Za-z]:/", s)):
        s = cwd.replace("\\", "/").rstrip("/") + "/" + s
    return [seg for seg in posixpath.normpath(s).split("/") if seg]


def _relative(path: str, cwd: str, config: GuardConfig) -> str | None:
    """root からの相対パス（`/` 区切り）。root の外なら None。root 自身は ""。"""
    root = _segments(str(config.root), "")
    segs = _segments(path, cwd)
    head = segs[: len(root)]
    if len(head) != len(root) or any(a.casefold() != b.casefold() for a, b in zip(head, root)):
        return None
    return "/".join(segs[len(root):])


def _protected_path(rel: str | None, config: GuardConfig, include_dirs: bool) -> bool:
    if rel is None:
        return False
    if any(glob_match(p, rel) for p in config.paths):
        return True
    if include_dirs:
        return any(p.endswith("/**") and glob_match(p[:-3], rel) for p in config.paths)
    return False


def _matched_word(text: object, config: GuardConfig) -> str | None:
    if not isinstance(text, str):
        return None
    folded = text.casefold()
    return next((w for w in config.command_words if w and w.casefold() in folded), None)


def decide(payload: dict, config: GuardConfig) -> str | None:
    """拒むなら理由を返し、通すなら None を返す。"""
    tool = payload.get("tool_name")
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        tool_input = {}
    cwd = payload.get("cwd")
    if not isinstance(cwd, str) or not cwd:
        cwd = os.getcwd()

    if tool in FILE_TOOLS:
        target = tool_input.get(FILE_TOOLS[tool])
        if isinstance(target, str) and target:
            rel = _relative(target, cwd, config)
            if _protected_path(rel, config, include_dirs=False):
                return REASON.format(rel)
        return None

    if tool in SEARCH_TOOLS:
        target = tool_input.get("path")
        if not isinstance(target, str) or not target:
            target = cwd
        rel = _relative(target, cwd, config)
        if _protected_path(rel, config, include_dirs=True):
            return REASON.format(rel)
        for key in ("pattern", "glob"):
            word = _matched_word(tool_input.get(key), config)
            if word is not None:
                return REASON.format(word)
        return None

    if tool in SHELL_TOOLS:
        word = _matched_word(tool_input.get("command"), config)
        if word is not None:
            return REASON.format(word)
        return None

    return None


def _deny(reason: str):
    import sys

    import typer

    # 受け手（Claude Code）が UTF-8 で読めるよう、コンソールのコードページを通さずに書く
    sys.stderr.flush()
    sys.stderr.buffer.write((reason + "\n").encode("utf-8"))
    sys.stderr.buffer.flush()
    raise typer.Exit(2)


def guard_command():
    """Claude Code の PreToolUse フック。標準入力の JSON を見て、守られたファイルへの呼び出しを拒む。"""
    import json
    import sys

    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError):
        _deny("jtk guard: 標準入力を JSON として読めません")
    if not isinstance(payload, dict):
        _deny("jtk guard: 標準入力を JSON として読めません")

    cwd = payload.get("cwd")
    try:
        config = load_guard_config(Path(cwd) if isinstance(cwd, str) and cwd else Path.cwd())
    except JtkError as e:
        _deny(str(e))
    if config is None:
        return
    reason = decide(payload, config)
    if reason is not None:
        _deny(reason)
