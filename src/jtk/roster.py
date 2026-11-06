"""暗号化された対応表（学生名簿）のモデルと、人の入力の確認。"""

import getpass
import sys
from dataclasses import dataclass
from pathlib import Path

from jtk.crypto import hash_email, normalize_email
from jtk.errors import JtkError
from jtk.table import Table
from jtk.table.secure_io import (
    read_encrypted_workbook,
    read_encrypted_xlsx,
    write_encrypted_workbook,
    write_encrypted_xlsx,
)

COLUMNS = ["学年", "姓", "名", "メールアドレス", "別表記", "呼称", "ID"]


@dataclass
class Student:
    cohort: str
    family: str
    given: str
    email: str
    variants: list[str]
    alias: str
    id: str
    row: int


def roster_column_indices(headers: list[object]) -> dict[str, int]:
    """見出しから COLUMNS の各列の位置を返す。足りない列があれば JtkError。"""
    names = [str(h).strip() if h is not None else "" for h in headers]
    indices = {}
    for col in COLUMNS:
        if col not in names:
            raise JtkError(f"列 {col} がありません")
        indices[col] = names.index(col)
    return indices


def _text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _split_variants(value: object) -> list[str]:
    text = _text(value).replace(",", "、")
    return [v.strip() for v in text.split("、") if v.strip()]


def students_from_table(table: Table) -> list[Student]:
    idx = roster_column_indices(table.headers)
    students = []
    for cells, row in zip(table.rows, table.row_numbers):
        def cell(col: str) -> object:
            i = idx[col]
            return cells[i] if i < len(cells) else None

        students.append(
            Student(
                cohort=_text(cell("学年")),
                family=_text(cell("姓")),
                given=_text(cell("名")),
                email=_text(cell("メールアドレス")),
                variants=_split_variants(cell("別表記")),
                alias=_text(cell("呼称")),
                id=_text(cell("ID")),
                row=row,
            )
        )
    return students


def open_roster(path: Path, password: str) -> list[Student]:
    return students_from_table(read_encrypted_xlsx(path, password))


def _stdin_isatty() -> bool:
    return sys.stdin.isatty()


def require_tty() -> None:
    if not _stdin_isatty():
        raise JtkError(
            "この操作は人の入力が要ります。PowerShell か Windows Terminal で実行してください"
        )


def prompt_password(confirm: bool = False) -> str:
    require_tty()
    password = getpass.getpass("対応表のパスワード: ")
    if confirm and getpass.getpass("対応表のパスワード（確認）: ") != password:
        raise JtkError("パスワードが一致しません")
    return password


def fill_ids(table: Table, scope_key: bytes) -> list[str]:
    """「ID」が空の行に ID を入れ、入れた ID を返す。検査に失敗したら表を変えない。"""
    idx = roster_column_indices(table.headers)
    students = students_from_table(table)
    seen_emails: set[str] = set()
    seen_aliases: set[tuple[str, str]] = set()
    expected: list[str] = []
    for s in students:
        if not s.email:
            raise JtkError(f"行 {s.row} のメールアドレスが空です")
        normalized = normalize_email(s.email)
        if normalized in seen_emails:
            raise JtkError(f"行 {s.row} のメールアドレスが他の行と重なっています")
        seen_emails.add(normalized)
        computed = hash_email(scope_key, s.email)
        if s.id and s.id != computed:
            raise JtkError(f"行 {s.row} の ID がメールアドレスと合いません")
        if not s.alias:
            raise JtkError(f"行 {s.row} の呼称が空です")
        if (s.cohort, s.alias) in seen_aliases:
            raise JtkError(f"行 {s.row} の呼称が同じ学年の他の行と重なっています")
        seen_aliases.add((s.cohort, s.alias))
        expected.append(computed)

    filled = []
    id_col = idx["ID"]
    for s, cells, computed in zip(students, table.rows, expected):
        if s.id:
            continue
        while len(cells) <= id_col:
            cells.append(None)
        cells[id_col] = computed
        filled.append(computed)
    return filled


def save_roster(table: Table, path: Path, password: str) -> None:
    """対応表を暗号化して保存する。失敗しても元のファイルは変わらない。"""
    write_encrypted_xlsx(table, path, password)


def fill_roster_file(path: Path, password: str, scope_key: bytes) -> list[str]:
    """暗号化された対応表の ID の空欄を埋めて上書きし、入れた ID を返す。

    最初のシートの ID のセルだけを書き換え、ほかのシート・書式・数式・空行・コメントは残す。
    埋める行が無ければ、ファイルは書き直さない。
    """
    table, workbook = read_encrypted_workbook(path, password)
    try:
        id_col = roster_column_indices(table.headers)["ID"]
        before = [cells[id_col] for cells in table.rows]
        filled = fill_ids(table, scope_key)
        if not filled:
            return filled
        ws = workbook.worksheets[0]
        for old, cells, row in zip(before, table.rows, table.row_numbers):
            if cells[id_col] == old:
                continue
            cell = ws.cell(row=row, column=id_col + 1)
            cell.value = str(cells[id_col])
            cell.data_type = "s"
        write_encrypted_workbook(workbook, path, password)
        return filled
    finally:
        workbook.close()
