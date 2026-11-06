"""`jtk table pii drop / hash` コマンド。"""

import errno
import os
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Optional

import typer

from jtk import crypto, keys, prompt
from jtk.errors import JtkError, exit_on_error
from jtk.guard import verify_guard
from jtk.table import Table, secure_io
from jtk.table.io import read_table, sheet_warnings, write_table
from jtk.table.leaks import collect_needles, find_leaks
from jtk.table.pii import HashFill, Roles, plan_hash_fill, resolve_roles, transform
from jtk.table.settings import (
    PiiSettings,
    load_pii_settings,
    moved_original_path,
    output_path,
)

app = typer.Typer(help="表の道具", pretty_exceptions_enable=False)
pii_app = typer.Typer(help="個人情報の列を消す・ハッシュに置き換える", pretty_exceptions_enable=False)
app.add_typer(pii_app, name="pii")

Input = Annotated[Path, typer.Argument(metavar="INPUT", help="入力の表（.xlsx / .csv）")]
Config = Annotated[Optional[Path], typer.Option("--config", help="設定ファイル（TOML）")]
Out = Annotated[Optional[Path], typer.Option("--out", help="出力先")]
Preset = Annotated[Optional[str], typer.Option("--preset", help="列の組（msforms）")]
Pii = Annotated[Optional[list[str]], typer.Option("--pii", help="消す個人情報の列（複数可）")]
Omit = Annotated[Optional[list[str]], typer.Option("--omit", help="消すその他の列（複数可）")]
MoveOriginal = Annotated[Optional[Path], typer.Option("--move-original", help="原本の移し先")]
Force = Annotated[bool, typer.Option("--force", help="既存のファイルを上書きする")]
Encrypt = Annotated[
    bool, typer.Option("--encrypt", help="出力をパスワード付きの xlsx にする（パスワードは画面で入力）")
]


def _same(a: Path, b: Path) -> bool:
    return a.resolve() == b.resolve()


def _check_paths(
    inp: Path, out: Path, moved: Path | None, force: bool, keep_pii: bool = False
) -> None:
    """keep_pii（--keep-pii --encrypt）で --force があるときに限り、--out を入力と同じにできる。"""
    if not inp.is_file():
        raise JtkError(f"入力 {inp.name} が見つかりません")
    if out.suffix.lower() != inp.suffix.lower():
        raise JtkError("--out の拡張子は入力と同じにしてください")
    if _same(out, inp):
        if not (keep_pii and force):
            hint = "。上書きするなら --force を付けてください" if keep_pii else ""
            raise JtkError(f"--out が入力と同じ場所です{hint}")
        if moved is not None:
            raise JtkError("--out を入力と同じにするときは、--move-original を使えません")
    if moved is not None:
        if _same(moved, inp):
            raise JtkError("--move-original が入力と同じ場所です")
        if _same(moved, out):
            raise JtkError("--move-original が --out と同じ場所です")
    if out.exists() and not force:
        raise JtkError(f"{out.name} がすでにあります。上書きするなら --force を付けてください")
    if moved is not None and moved.exists() and not force:
        raise JtkError(f"{moved.name} がすでにあります。上書きするなら --force を付けてください")


def _make_parent(out: Path) -> None:
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        raise JtkError(f"{out.parent.name} を作れません") from None


def _write_atomic(table, out: Path) -> None:
    _make_parent(out)
    tmp = out.parent / f".{out.stem}.jtk-tmp{out.suffix}"
    try:
        try:
            write_table(table, tmp)
        except JtkError:
            raise JtkError(f"{out.name} に書き込めません") from None
        os.replace(tmp, out)
    except JtkError:
        raise
    except OSError:
        raise JtkError(f"{out.name} に書き込めません") from None
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def _discard(tmp: Path) -> None:
    """一時ファイルを消す。消せなければ、残った場所を知らせる。"""
    try:
        tmp.unlink(missing_ok=True)
    except OSError:
        raise JtkError(
            f"一時ファイル {tmp} を消せませんでした。個人情報が入っているので手で消してください。"
        ) from None


def _move_across_devices(inp: Path, moved: Path) -> None:
    """別のドライブへは複写してから消す。最後の 1 部は消さない。"""
    tmp = moved.parent / f".{moved.name}.jtk-tmp"
    try:
        shutil.copy2(inp, tmp)
        inp.unlink()
    except Exception:
        _discard(tmp)
        raise
    try:
        os.replace(tmp, moved)
        return
    except OSError:
        pass
    # 原本は消えている。戻せたと確かめられるまで tmp を消さない。
    try:
        size = tmp.stat().st_size
        shutil.copy2(tmp, inp)
        restored = inp.stat().st_size == size
    except Exception:
        restored = False
    if not restored:
        raise JtkError(
            "原本を移せず、元の場所へも戻せませんでした。"
            f"原本の内容は一時ファイル {tmp} に残してあります（消さないでください）。"
            "出力は消しました。"
        ) from None
    _discard(tmp)
    raise OSError


def _move_original(inp: Path, moved: Path, out: Path) -> None:
    try:
        moved.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.replace(inp, moved)
        except OSError as e:
            if e.errno != errno.EXDEV:
                raise
            _move_across_devices(inp, moved)
    except JtkError:
        out.unlink(missing_ok=True)
        raise
    except Exception:
        out.unlink(missing_ok=True)
        raise JtkError(
            "原本を移せませんでした（Excel などで開いていないか確かめてください）。"
            "出力は消しました。"
        ) from None


def _confirm_keep_pii(out: Path) -> None:
    """--keep-pii の守り。端末か、フックで守られているか、ファイル名の打ち直し。"""
    prompt.require_tty()
    failures = verify_guard(out)
    if failures:
        raise JtkError("\n".join(["出力先が Claude Code のフックで守られていません。", *failures]))
    if typer.prompt(f"確認のため、出力先のファイル名 {out.name} を打ち直してください") != out.name:
        raise JtkError("ファイル名が一致しません")


def _keep_pii(
    inp: Path,
    out: Path,
    moved: Path | None,
    password: str,
    encrypted_input: bool,
    resolve: Callable[[list[str]], Roles],
    scope_key: bytes,
    hash_column: str,
) -> tuple[Table, HashFill]:
    """hash 列の空欄だけを埋め、ブックを保ったまま暗号化して書く。埋める行が無ければ書かない。"""
    table, workbook = secure_io.read_workbook(inp, password if encrypted_input else None)
    try:
        for w in sheet_warnings(workbook):
            typer.echo(w, err=True)
        hash_col = resolve(table.headers).hash
        fill = plan_hash_fill(table, hash_col, hash_column, scope_key)
        if fill.cells:
            ws = workbook.worksheets[0]
            column = fill.column + 1
            if fill.added:
                header = ws.cell(row=1, column=column, value=hash_column)
                header.data_type = "s"
            for row, value in fill.cells:
                cell = ws.cell(row=row, column=column, value=value)
                cell.data_type = "s"
            _make_parent(out)
            secure_io.write_encrypted_workbook(workbook, out, password)
    finally:
        workbook.close()
    if fill.cells and moved is not None:
        _move_original(inp, moved, out)
    return table, fill


def _run(
    mode: str,
    inp: Path,
    config: Path | None,
    out: Path | None,
    preset: str | None,
    pii: list[str] | None,
    omit: list[str] | None,
    move_original: Path | None,
    force: bool,
    scope: str | None = None,
    hash_cols: list[str] | None = None,
    hash_column: str | None = None,
    keep_pii: bool = False,
    encrypt: bool = False,
) -> None:
    if keep_pii and not encrypt:
        raise JtkError(
            "--keep-pii には --encrypt が要ります。個人情報を残すなら、出力を暗号化してください"
        )
    settings = load_pii_settings(config) if config else PiiSettings()
    if out is None:
        if settings.out_dir is None:
            raise JtkError("--out か、設定ファイルの out_dir が要ります")
        out = output_path(settings, inp)
    moved = move_original if move_original is not None else moved_original_path(settings, inp)
    preset = preset or settings.preset
    pii_cols = settings.pii + (pii or [])
    omit_cols = settings.omit + (omit or [])
    if mode == "hash":
        scope = scope or settings.scope
        if not scope:
            raise JtkError("--scope か、設定ファイルの scope が要ります")
        hash_cols = hash_cols or ([settings.hash] if settings.hash else [])
        hash_column = hash_column or settings.hash_column or "hash"
    else:
        hash_cols = []
    if encrypt and (inp.suffix.lower() != ".xlsx" or out.suffix.lower() != ".xlsx"):
        raise JtkError("--encrypt は .xlsx だけに使えます")
    if keep_pii and omit_cols:
        raise JtkError("--keep-pii では列を消しません。--omit は使えません")

    _check_paths(inp, out, moved, force, keep_pii=keep_pii)
    if keep_pii:
        _confirm_keep_pii(out)
    elif encrypt:
        prompt.require_tty()

    scope_key = master = None
    if mode == "hash":
        master = keys.load_master_key()
        scope_key = crypto.derive_scope_key(master, scope)

    encrypted_input = inp.suffix.lower() == ".xlsx" and secure_io.is_encrypted(inp)
    password = None
    if encrypted_input:
        password = prompt.prompt_password()
    elif encrypt:
        password = prompt.prompt_password(confirm=True)

    def roles_for(headers):
        return resolve_roles(
            headers,
            mode=mode,
            preset=preset,
            hash_cols=hash_cols,
            pii=pii_cols,
            omit=omit_cols,
        )

    if keep_pii:
        table, fill = _keep_pii(
            inp, out, moved, password, encrypted_input, roles_for, scope_key, hash_column
        )
        written = bool(fill.cells)
        if written:
            typer.echo(f"出力: {out}")
            typer.echo("暗号化: しました")
        else:
            typer.echo("出力: 書き換えていません（埋める行がありません）")
        typer.echo(f"行数: {len(table.rows)}")
        typer.echo(f"埋めた件数: {len(fill.cells)} 件")
        typer.echo("削除した列: なし")
        if written and fill.added:
            typer.echo(f"追加した列: {hash_column}")
        typer.echo(f"スコープ: {scope}")
        typer.echo(f"鍵の指紋: {crypto.fingerprint(master)}")
        typer.echo("漏れの検査: しませんでした（--keep-pii）")
        if written and moved is not None:
            typer.echo(f"原本の移し先: {moved}")
        if settings.source is not None:
            typer.echo(f"設定ファイル: {settings.source}")
        return

    if encrypted_input:
        table, warnings = secure_io.read_encrypted_table(inp, password)
    else:
        table, warnings = read_table(inp)
    for w in warnings:
        typer.echo(w, err=True)

    roles = roles_for(table.headers)
    result = transform(table, roles, scope_key=scope_key, hash_column=hash_column or "hash")

    needles = collect_needles(table, roles)
    skip = {hash_column or "hash"} if roles.hash else set()
    leaks = find_leaks(result, needles, skip)
    if leaks:
        lines = ["個人情報が残っています。何も書き出していません。"]
        lines += [f"行 {lk.row} / 列 {lk.column} / {lk.kind}" for lk in leaks]
        lines.append("原本を直すか、その列を --omit で消してください。")
        raise JtkError("\n".join(lines))

    if encrypt:
        _make_parent(out)
        secure_io.write_encrypted_xlsx(result, out, password)
    else:
        _write_atomic(result, out)
    if moved is not None:
        _move_original(inp, moved, out)

    removed = roles.pii + roles.omit
    typer.echo(f"出力: {out}")
    if encrypt:
        typer.echo("暗号化: しました")
    typer.echo(f"行数: {len(result.rows)}")
    typer.echo(f"削除した列: {', '.join(removed) if removed else 'なし'}")
    if mode == "hash":
        typer.echo(f"追加した列: {hash_column}")
        typer.echo(f"スコープ: {scope}")
        typer.echo(f"鍵の指紋: {crypto.fingerprint(master)}")
    typer.echo("漏れの検査: 見つかりませんでした")
    if moved is not None:
        typer.echo(f"原本の移し先: {moved}")
    if settings.source is not None:
        typer.echo(f"設定ファイル: {settings.source}")


@pii_app.command()
@exit_on_error
def drop(
    input: Input,
    config: Config = None,
    out: Out = None,
    preset: Preset = None,
    pii: Pii = None,
    omit: Omit = None,
    move_original: MoveOriginal = None,
    force: Force = False,
    encrypt: Encrypt = False,
):
    """個人情報の列を消して書き出す。鍵は使わない。"""
    _run("drop", input, config, out, preset, pii, omit, move_original, force, encrypt=encrypt)


@pii_app.command()
@exit_on_error
def hash(
    input: Input,
    config: Config = None,
    out: Out = None,
    preset: Preset = None,
    pii: Pii = None,
    omit: Omit = None,
    move_original: MoveOriginal = None,
    force: Force = False,
    scope: Annotated[Optional[str], typer.Option("--scope", help="ハッシュの範囲（例: 2026後期/授業名）")] = None,
    hash_: Annotated[Optional[list[str]], typer.Option("--hash", help="ハッシュにする列")] = None,
    hash_column: Annotated[Optional[str], typer.Option("--hash-column", help="ハッシュ列の名前（既定 hash）")] = None,
    keep_pii: Annotated[
        bool,
        typer.Option("--keep-pii", help="個人情報の列を残し、hash 列の空欄を埋める（--encrypt が要る）"),
    ] = False,
    encrypt: Encrypt = False,
):
    """メールアドレスの列をハッシュに置き換え、個人情報の列を消して書き出す。"""
    _run(
        "hash", input, config, out, preset, pii, omit, move_original, force, scope, hash_,
        hash_column, keep_pii=keep_pii, encrypt=encrypt,
    )
