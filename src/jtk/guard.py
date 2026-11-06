"""Claude Code の PreToolUse フック。守られたファイルに触る道具の呼び出しを拒む。

理由の文には設定にある項目（`X/**` なら `X`）か語だけを入れる。実際のパス
（学生の名前を含みうる）、ファイルの中身、コマンドの全文は入れない。
"""

import json
import os
import posixpath
import re
import shutil
import subprocess
import sys
import tempfile
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


# --- フックが効いているかの検査 ---

HOOK_TIMEOUT = 20
NO_SETTINGS = "`.claude/settings.json` も `.claude/settings.local.json` もありません"
BAD_SETTINGS = "`.claude/settings.json` か `.claude/settings.local.json` を JSON として読めません"
NO_HOOKS = "`.claude/settings.json` に、Read・Bash・Grep に当たる PreToolUse のフックがありません"
NO_SHELL = "フックを動かすシェル（Git Bash）が見つかりません"
HOOK_TIMED_OUT = "フックが {} 秒で終わりません"
HOOK_NOT_STARTED = "フックのコマンドを起動できません"
NOT_DENIED = "{} が拒まれません"


def verify_guard(target: Path) -> list[str]:
    """target が PreToolUse のフックで守られているかを、偽の呼び出しで確かめる。

    失敗の説明の一覧を返す。空なら合格。説明には実際のパスを入れない。
    """
    target = Path(os.path.abspath(target))
    root = _find_settings_root(target)
    if root is None:
        return [NO_SETTINGS]
    try:
        entries = _pre_tool_use_entries(root / ".claude")
    except (OSError, UnicodeDecodeError, ValueError):
        return [BAD_SETTINGS]

    calls = [
        ("Read", {"file_path": str(target)}),
        ("Bash", {"command": f'cat "{target}"'}),
        ("Grep", {"pattern": "a", "path": str(target.parent)}),
    ]
    commands = {tool: _commands_for(tool, entries) for tool, _ in calls}
    if not any(commands.values()):
        return [NO_HOOKS]
    shell = _hook_shell()
    if shell is None:
        return [NO_SHELL]

    failures: list[str] = []
    problems: list[str] = []
    for tool, tool_input in calls:
        payload = {"tool_name": tool, "tool_input": tool_input, "cwd": str(root), "hook_event_name": "PreToolUse"}
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        denied = False
        for command in commands[tool]:
            outcome = _run_hook(shell, command, data, root)
            if outcome is True:
                denied = True
                break
            if isinstance(outcome, str) and outcome not in problems:
                problems.append(outcome)
        if not denied:
            failures.append(NOT_DENIED.format(tool))
    return failures + problems


def _find_settings_root(target: Path) -> Path | None:
    """target から親へたどり、.claude/settings.json か settings.local.json のあるフォルダを返す。

    ホームの .claude/settings.json はユーザー全体の設定で、プロジェクトの設定ではないので数えない。
    """
    home = Path(os.path.abspath(Path.home()))
    for directory in target.parents:
        if os.path.normcase(directory) == os.path.normcase(home):
            continue
        claude_dir = directory / ".claude"
        if (claude_dir / "settings.json").is_file() or (claude_dir / "settings.local.json").is_file():
            return directory
    return None


def _pre_tool_use_entries(claude_dir: Path) -> list[dict]:
    """settings.json と settings.local.json の hooks.PreToolUse を合わせる。"""
    entries: list[dict] = []
    for name in ("settings.json", "settings.local.json"):
        path = claude_dir / name
        if not path.is_file():
            continue
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        hooks = data.get("hooks") if isinstance(data, dict) else None
        pre = hooks.get("PreToolUse") if isinstance(hooks, dict) else None
        if isinstance(pre, list):
            entries.extend(e for e in pre if isinstance(e, dict))
    return entries


def _matcher_hits(matcher: object, tool: str) -> bool:
    if matcher is None or matcher == "" or matcher == "*":
        return True
    if not isinstance(matcher, str):
        return False
    try:
        return re.fullmatch(matcher, tool) is not None
    except re.error:
        return False


def _commands_for(tool: str, entries: list[dict]) -> list[str]:
    commands = []
    for entry in entries:
        if not _matcher_hits(entry.get("matcher"), tool):
            continue
        hooks = entry.get("hooks")
        for hook in hooks if isinstance(hooks, list) else []:
            if isinstance(hook, dict) and hook.get("type") == "command" and isinstance(hook.get("command"), str):
                commands.append(hook["command"])
    return commands


def _hook_shell() -> str | None:
    """Claude Code がフックを動かすのと同じシェル。Windows では Git Bash、ほかでは /bin/sh。"""
    if os.name != "nt":
        return "/bin/sh"
    found = shutil.which("bash")
    if found is not None and not _is_wsl_launcher(Path(found)):
        return found
    fallback = Path(r"C:\Program Files\Git\bin\bash.exe")
    return str(fallback) if fallback.is_file() else None


def _is_wsl_launcher(path: Path) -> bool:
    """System32 や WindowsApps の bash.exe は WSL の入り口で、Git Bash ではない。"""
    parts = {p.casefold() for p in path.parts}
    return "system32" in parts or "windowsapps" in parts


def _run_hook(shell: str, command: str, data: bytes, root: Path) -> bool | str:
    """フックのコマンドを 1 つ動かす。拒んだら True、通したら False、動かせなければ説明。

    出力は一時ファイルで受ける。フックが残した子プロセスがパイプを握っていても待たされない。
    """
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(root)}
    with tempfile.TemporaryFile() as stdin, tempfile.TemporaryFile() as stdout:
        stdin.write(data)
        stdin.seek(0)
        try:
            process = subprocess.Popen(
                [shell, "-c", command], stdin=stdin, stdout=stdout, stderr=subprocess.DEVNULL, cwd=root, env=env
            )
        except OSError:
            return HOOK_NOT_STARTED
        try:
            code = process.wait(timeout=HOOK_TIMEOUT)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            return HOOK_TIMED_OUT.format(HOOK_TIMEOUT)
        if code == 2:
            return True
        stdout.seek(0)
        return _says_deny(stdout.read())


def _says_deny(output: bytes) -> bool:
    try:
        result = json.loads(output.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError):
        return False
    if not isinstance(result, dict):
        return False
    specific = result.get("hookSpecificOutput")
    if isinstance(specific, dict) and specific.get("permissionDecision") == "deny":
        return True
    return result.get("permissionDecision") == "deny"
