from dataclasses import dataclass
from typing import Literal

from jtk import crypto
from jtk.errors import JtkError
from jtk.table import Table

Mode = Literal["drop", "hash"]

PRESETS = {"msforms": {"email": ["Email", "メール"], "name": ["Name", "名前"]}}


@dataclass
class Roles:
    hash: str | None
    pii: list[str]
    omit: list[str]


def _preset_column(headers: list[str], preset: str, item: str) -> str:
    candidates = PRESETS[preset][item]
    found = [c for c in candidates if c in headers]
    if not found:
        raise JtkError(
            f"プリセット {preset} の列（{' / '.join(candidates)}）が見つかりません"
        )
    if len(found) > 1:
        raise JtkError(
            f"プリセット {preset} の列が複数あります（{' / '.join(found)}）。"
            "--pii / --omit で指定してください"
        )
    return found[0]


def _unique(names: list[str]) -> list[str]:
    return list(dict.fromkeys(names))


def resolve_roles(
    headers: list[str],
    *,
    mode: Mode,
    preset: str | None,
    hash_cols: list[str],
    pii: list[str],
    omit: list[str],
) -> Roles:
    hash_col: str | None = None
    pii_cols: list[str] = []
    if preset is not None:
        if preset not in PRESETS:
            raise JtkError(f"プリセット {preset} は未定義です")
        if hash_cols:
            raise JtkError("--preset と --hash は同時に指定できません")
        email = _preset_column(headers, preset, "email")
        name = _preset_column(headers, preset, "name")
        if mode == "hash":
            hash_col = email
            pii_cols = [name]
        else:
            pii_cols = [email, name]
    elif mode == "hash":
        unique_hash = _unique(hash_cols)
        if not unique_hash:
            raise JtkError("ハッシュにする列を --hash か --preset で指定してください")
        if len(unique_hash) > 1:
            raise JtkError("--hash に指定できる列は 1 つだけです")
        hash_col = unique_hash[0]
    pii_cols = _unique(pii_cols + pii)
    omit_cols = _unique(omit)
    if mode == "drop" and not pii_cols and not omit_cols:
        raise JtkError("消す列が指定されていません")

    claimed: set[str] = set()
    for cols in ([hash_col] if hash_col is not None else [], pii_cols, omit_cols):
        for col in cols:
            if col not in headers:
                raise JtkError(f"列 {col} が見出しにありません")
            if headers.count(col) > 1:
                raise JtkError(f"列 {col} が見出しに複数あり、役割を決められません")
            if col in claimed:
                raise JtkError(f"列 {col} が複数の役割に指定されています")
            claimed.add(col)
    return Roles(hash_col, pii_cols, omit_cols)


def _rows_text(numbers: list[int]) -> str:
    return ", ".join(str(n) for n in numbers)


def _hash_cells(table: Table, column: str, scope_key: bytes) -> list[str]:
    idx = table.headers.index(column)
    empty = [
        n
        for n, row in zip(table.row_numbers, table.rows)
        if row[idx] is None or not str(row[idx]).strip()
    ]
    if empty:
        raise JtkError(
            f"ハッシュ対象の列 {column} に空のセルがあります（行 {_rows_text(empty)}）"
        )
    first_seen: dict[str, tuple[str, int]] = {}
    hashes: list[str] = []
    for n, row in zip(table.row_numbers, table.rows):
        text = str(row[idx])
        hashed = crypto.hash_email(scope_key, text)
        normalized = crypto.normalize_email(text)
        if hashed in first_seen and first_seen[hashed][0] != normalized:
            raise JtkError(
                "異なるアドレスが同じハッシュ値になりました"
                f"（行 {first_seen[hashed][1]}, {n}）"
            )
        first_seen.setdefault(hashed, (normalized, n))
        hashes.append(hashed)
    return hashes


def transform(
    table: Table, roles: Roles, *, scope_key: bytes | None, hash_column: str = "hash"
) -> Table:
    if roles.hash is not None and scope_key is None:
        raise ValueError("ハッシュには scope_key が必要です")
    removed = set(roles.pii) | set(roles.omit)
    keep = [i for i, h in enumerate(table.headers) if h not in removed]

    hashes: list[str] | None = None
    if roles.hash is not None:
        others = [table.headers[i] for i in keep if table.headers[i] != roles.hash]
        if hash_column in others:
            raise JtkError(f"ハッシュ列の名前 {hash_column} が既存の列と重なります")
        hashes = _hash_cells(table, roles.hash, scope_key)

    headers = [
        hash_column if table.headers[i] == roles.hash else table.headers[i]
        for i in keep
    ]
    rows = []
    for r, row in enumerate(table.rows):
        rows.append(
            [
                hashes[r] if hashes is not None and table.headers[i] == roles.hash else row[i]
                for i in keep
            ]
        )
    return Table(headers, rows, list(table.row_numbers), table.sheet_title)


@dataclass
class HashFill:
    """--keep-pii のときに書くセル。column は 0 始まりの列の位置。"""

    column: int
    added: bool
    cells: list[tuple[int, str]]  # (表計算ソフト上の行番号, hash の値)


def _blank(value: object) -> bool:
    return value is None or not str(value).strip()


def plan_hash_fill(
    table: Table, hash_col: str, hash_column: str, scope_key: bytes
) -> HashFill:
    """hash 列の空欄に入れる値を決める。表は変えない。

    hash 列が無ければ右端に足す。値が入っていて計算した値と合わない行があれば止める。
    """
    if hash_column == hash_col:
        raise JtkError(f"ハッシュ列の名前 {hash_column} がハッシュにする列と同じです")
    count = table.headers.count(hash_column)
    if count > 1:
        raise JtkError(f"列 {hash_column} が見出しに複数あります")
    hashes = _hash_cells(table, hash_col, scope_key)
    if count == 0:
        return HashFill(len(table.headers), True, list(zip(table.row_numbers, hashes)))

    idx = table.headers.index(hash_column)
    cells: list[tuple[int, str]] = []
    mismatched: list[int] = []
    for n, row, hashed in zip(table.row_numbers, table.rows, hashes):
        value = row[idx]
        if _blank(value):
            cells.append((n, hashed))
        elif str(value).strip() != hashed:
            mismatched.append(n)
    if mismatched:
        raise JtkError(
            f"列 {hash_column} の値が {hash_col} から計算した値と合いません"
            f"（行 {_rows_text(mismatched)}）"
        )
    return HashFill(idx, False, cells)
