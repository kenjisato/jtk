"""Claude Code の PreToolUse フック。守られたファイルに触る道具の呼び出しを拒む。

理由の文には設定にある項目（`X/**` なら `X`）か語だけを入れる。実際のパス
（学生の名前を含みうる）、ファイルの中身、コマンドの全文は入れない。
"""

import json
import os
import posixpath
import re
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

import typer

from jtk.errors import JtkError

CONFIG_RELPATH = Path(".claude") / "jtk-guard.toml"
REASON = "jtk guard: 守られたファイルです（{}）"
SEARCH_ROOT_REASON = (
    "jtk guard: この場所の下には守られたフォルダがあります。"
    "守られたフォルダの外を指す path を指定して検索してください"
)
PATTERN_REASON = "jtk guard: 検索パターンに .. や絶対パスは使えません。path で検索先を指定してください"
BAD_INPUT_REASON = "jtk guard: 標準入力を JSON として読めません"
UNDECIDABLE_REASON = "jtk guard: 判定できません"

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




def _matched_entry(rel: str | None, config: GuardConfig, include_dirs: bool) -> str | None:
    """rel に当たった paths の項目を、理由に出す形で返す。当たらなければ None。

    実際のパスは名前を含みうるので返さない。`X/**` の項目なら `X`、ほかは項目そのもの。
    include_dirs なら `X/**` の `X` 自身にも当てる（Grep / Glob の検索先）。
    """
    if rel is None:
        return None
    for p in config.paths:
        if p.endswith("/**") and (glob_match(p, rel) or (include_dirs and glob_match(p[:-3], rel))):
            return p[:-3]
    for p in config.paths:
        if glob_match(p, rel):
            return p
    return None


def _contains_protected_dir(rel: str | None, config: GuardConfig) -> bool:
    """rel が、ある `X/**` の `X` の祖先（root を含む）か。root の外なら False。"""
    if rel is None:
        return False
    for p in config.paths:
        if not p.endswith("/**"):
            continue
        segments = p[:-3].split("/")
        for k in range(len(segments)):
            prefix = "/".join(segments[:k])
            if (rel == "") if k == 0 else glob_match(prefix, rel):
                return True
    return False


def _escapes_search_root(pattern: object) -> bool:
    """glob が `..` の区切りを含むか、絶対パスか。"""
    if not isinstance(pattern, str):
        return False
    s = pattern.replace("\\", "/")
    return s.startswith("/") or re.match(r"[A-Za-z]:", s) is not None or ".." in s.split("/")


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
            entry = _matched_entry(_relative(target, cwd, config), config, include_dirs=False)
            if entry is not None:
                return REASON.format(entry)
        return None

    if tool in SEARCH_TOOLS:
        target = tool_input.get("path")
        if not isinstance(target, str) or not target:
            target = cwd
        rel = _relative(target, cwd, config)
        entry = _matched_entry(rel, config, include_dirs=True)
        if entry is not None:
            return REASON.format(entry)
        if _contains_protected_dir(rel, config):
            return SEARCH_ROOT_REASON
        for key in ("pattern", "glob"):
            if _escapes_search_root(tool_input.get(key)):
                return PATTERN_REASON
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


def _deny(reason: str) -> NoReturn:
    # 受け手（Claude Code）が UTF-8 で読めるよう、コンソールのコードページを通さずに書く
    sys.stderr.flush()
    sys.stderr.buffer.write((reason + "\n").encode("utf-8"))
    sys.stderr.buffer.flush()
    raise typer.Exit(2)


def guard_command():
    """Claude Code の PreToolUse フック。標準入力の JSON を見て、守られたファイルへの呼び出しを拒む。"""
    try:
        reason = _judge(sys.stdin.buffer.read())
    except Exception:
        reason = UNDECIDABLE_REASON
    if reason is not None:
        _deny(reason)


def _judge(data: bytes) -> str | None:
    """標準入力の中身から、拒む理由か None を返す。判定できない入力は拒む。"""
    try:
        payload = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError):
        return BAD_INPUT_REASON
    if not isinstance(payload, dict):
        return BAD_INPUT_REASON
    cwd = payload.get("cwd")
    try:
        config = load_guard_config(Path(cwd) if isinstance(cwd, str) and cwd else Path.cwd())
    except JtkError as e:
        return str(e)
    if config is None:
        return None
    return decide(payload, config)
