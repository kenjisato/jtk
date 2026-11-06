"""人の入力（端末の確認とパスワード）。"""

import getpass
import sys

from jtk.errors import JtkError


def _stdin_isatty() -> bool:
    return sys.stdin.isatty()


def require_tty() -> None:
    if not _stdin_isatty():
        raise JtkError(
            "この操作は人の入力が要ります。PowerShell か Windows Terminal で実行してください"
        )


def prompt_password(confirm: bool = False) -> str:
    """パスワードを入力欄（画面に出ない）で受け取る。confirm なら 2 回聞いて照合する。"""
    require_tty()
    password = getpass.getpass("ファイルのパスワード: ")
    if confirm and getpass.getpass("ファイルのパスワード（確認）: ") != password:
        raise JtkError("パスワードが一致しません")
    return password
