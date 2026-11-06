"""暗号化された対応表（学生名簿）のモデルと、人の入力の確認。"""

import getpass
import sys
from dataclasses import dataclass
from pathlib import Path

from jtk.errors import JtkError
from jtk.table import Table
from jtk.table.secure_io import read_encrypted_xlsx

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
    return "" if value is None else str(value).strip()


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
