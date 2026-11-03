"""`jtk table pii drop / hash` コマンド。"""

import os
import shutil
from pathlib import Path
from typing import Annotated, Optional

import typer

from jtk import crypto, keys
from jtk.errors import JtkError, exit_on_error
from jtk.table.io import read_table, write_table
from jtk.table.leaks import collect_needles, find_leaks
from jtk.table.pii import resolve_roles, transform
from jtk.table.settings import (
    PiiSettings,
    load_pii_settings,
    moved_original_path,
    output_path,
)

app = typer.Typer(help="表の道具", pretty_exceptions_enable=False)
pii_app = typer.Typer(help="個人情報の列を消す・ハッシュに置き換える", pretty_exceptions_enable=False)
app.add_typer(pii_app, name="pii")

Input = Annotated[Path, typer.Argument(help="入力の表（.xlsx / .csv）")]
Config = Annotated[Optional[Path], typer.Option("--config", help="設定ファイル（TOML）")]
Out = Annotated[Optional[Path], typer.Option("--out", help="出力先")]
Preset = Annotated[Optional[str], typer.Option("--preset", help="列の組（msforms）")]
Pii = Annotated[Optional[list[str]], typer.Option("--pii", help="消す個人情報の列（複数可）")]
Omit = Annotated[Optional[list[str]], typer.Option("--omit", help="消すその他の列（複数可）")]
MoveOriginal = Annotated[Optional[Path], typer.Option("--move-original", help="原本の移し先")]
Force = Annotated[bool, typer.Option("--force", help="既存のファイルを上書きする")]


def _same(a: Path, b: Path) -> bool:
    return a.resolve() == b.resolve()


def _check_paths(inp: Path, out: Path, moved: Path | None, force: bool) -> None:
    if not inp.is_file():
        raise JtkError(f"入力 {inp.name} が見つかりません")
    if out.suffix.lower() != inp.suffix.lower():
        raise JtkError("--out の拡張子は入力と同じにしてください")
    if _same(out, inp):
        raise JtkError("--out が入力と同じ場所です")
    if moved is not None:
        if _same(moved, inp):
            raise JtkError("--move-original が入力と同じ場所です")
        if _same(moved, out):
            raise JtkError("--move-original が --out と同じ場所です")
    if out.exists() and not force:
        raise JtkError(f"{out.name} がすでにあります。上書きするなら --force を付けてください")
    if moved is not None and moved.exists() and not force:
        raise JtkError(f"{moved.name} がすでにあります。上書きするなら --force を付けてください")


def _write_atomic(table, out: Path) -> None:
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        raise JtkError(f"{out.parent.name} を作れません") from None
    tmp = out.parent / f".{out.stem}.jtk-tmp{out.suffix}"
    try:
        write_table(table, tmp)
        os.replace(tmp, out)
    except JtkError:
        raise
    except OSError:
        raise JtkError(f"{out.name} に書き込めません") from None
    finally:
        tmp.unlink(missing_ok=True)


def _move_original(inp: Path, moved: Path, out: Path) -> None:
    try:
        moved.parent.mkdir(parents=True, exist_ok=True)
        if moved.exists():
            moved.unlink()
        shutil.move(inp, moved)
    except Exception:
        out.unlink(missing_ok=True)
        raise JtkError(
            "原本を移せませんでした（Excel などで開いていないか確かめてください）。"
            "出力は消しました。"
        ) from None


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
) -> None:
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

    _check_paths(inp, out, moved, force)

    scope_key = master = None
    if mode == "hash":
        master = keys.load_master_key()
        scope_key = crypto.derive_scope_key(master, scope)

    table, warnings = read_table(inp)
    for w in warnings:
        typer.echo(w, err=True)

    roles = resolve_roles(
        table.headers, mode=mode, preset=preset, hash_cols=hash_cols, pii=pii_cols, omit=omit_cols
    )
    result = transform(table, roles, scope_key=scope_key, hash_column=hash_column or "hash")

    needles = collect_needles(table, roles)
    skip = {hash_column or "hash"} if roles.hash else set()
    leaks = find_leaks(result, needles, skip)
    if leaks:
        lines = ["個人情報が残っています。何も書き出していません。"]
        lines += [f"行 {lk.row} / 列 {lk.column} / {lk.kind}" for lk in leaks]
        lines.append("原本を直すか、その列を --omit で消してください。")
        raise JtkError("\n".join(lines))

    _write_atomic(result, out)
    if moved is not None:
        _move_original(inp, moved, out)

    removed = roles.pii + roles.omit
    typer.echo(f"出力: {out}")
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
):
    """個人情報の列を消して書き出す。鍵は使わない。"""
    _run("drop", input, config, out, preset, pii, omit, move_original, force)


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
):
    """メールアドレスの列をハッシュに置き換え、個人情報の列を消して書き出す。"""
    _run("hash", input, config, out, preset, pii, omit, move_original, force, scope, hash_, hash_column)
