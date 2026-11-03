"""親鍵を OS の鍵保管庫に置く。`jtk key` コマンド。"""

import keyring
import keyring.errors
import typer

from jtk.crypto import (
    decode_master_key,
    encode_master_key,
    fingerprint,
    generate_master_key,
)
from jtk.errors import JtkError, exit_on_error

SERVICE = "jtk"
USERNAME = "master"

KEY_MISSING_MESSAGE = (
    "この PC には jtk の親鍵がありません。Bitwarden に保存した親鍵をコピーし、"
    "`jtk key set` で登録してください。初めて使うなら `jtk key new` で作ります。"
)
_KEYRING_UNAVAILABLE = (
    "この PC では鍵保管庫を使えません。鍵保管庫の設定を確かめたうえで、"
    "Bitwarden に保存した親鍵をコピーし、`jtk key set` で登録してください。"
    "初めて使うなら `jtk key new` で作ります。"
)

app = typer.Typer(help="親鍵の管理", pretty_exceptions_enable=False)


def _read() -> str | None:
    try:
        return keyring.get_password(SERVICE, USERNAME)
    except keyring.errors.KeyringError:
        raise JtkError(_KEYRING_UNAVAILABLE) from None


def _write(text: str) -> None:
    try:
        keyring.set_password(SERVICE, USERNAME, text)
    except keyring.errors.KeyringError:
        raise JtkError(_KEYRING_UNAVAILABLE) from None


def load_master_key() -> bytes:
    text = _read()
    if text is None:
        raise JtkError(KEY_MISSING_MESSAGE)
    return decode_master_key(text)


@app.command()
@exit_on_error
def new() -> None:
    """親鍵を新しく作り、この PC の鍵保管庫に置く。"""
    if _read() is not None:
        raise JtkError(
            "この PC にはすでに親鍵があります。上書きする場合は `jtk key set --force` を使ってください。"
        )
    key = generate_master_key()
    text = encode_master_key(key)
    _write(text)
    typer.echo("親鍵を作り、この PC の鍵保管庫に置きました。")
    typer.echo("次の親鍵は今回しか表示しません。いま Bitwarden に保存してください。")
    typer.echo(text)
    typer.echo(f"指紋: {fingerprint(key)}")
    typer.echo("別の PC では、保存した親鍵を `jtk key set` で登録します。")


@app.command()
@exit_on_error
def set(
    force: bool = typer.Option(False, "--force", help="既存の親鍵を上書きする"),
) -> None:
    """Bitwarden に保存した親鍵を、この PC の鍵保管庫に登録する。"""
    if _read() is not None and not force:
        raise JtkError(
            "この PC にはすでに親鍵があります。上書きする場合は `--force` を付けてください。"
        )
    key = decode_master_key(typer.prompt("親鍵", hide_input=True))
    _write(encode_master_key(key))
    typer.echo(f"親鍵を登録しました。指紋: {fingerprint(key)}")


@app.command("fingerprint")
@exit_on_error
def show_fingerprint() -> None:
    """親鍵の指紋（照合用の 8 桁）を表示する。"""
    typer.echo(fingerprint(load_master_key()))
