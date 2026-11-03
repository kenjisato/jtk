import re
import unicodedata
from dataclasses import dataclass

from jtk.table import Table
from jtk.table.pii import Roles

EMAIL_KIND = "メールアドレスの形"
MIN_LENGTH = 4
_EMAIL = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+")


@dataclass(frozen=True)
class Leak:
    row: int
    column: str
    kind: str


def normalize(text: str) -> str:
    return re.sub(r"\s", "", unicodedata.normalize("NFKC", text)).lower()


def collect_needles(original: Table, roles: Roles) -> dict[str, str]:
    """個人情報の列の値を、正規化した探す値 → 種別の対応にする。"""
    columns = ([roles.hash] if roles.hash else []) + roles.pii
    needles: dict[str, str] = {}

    def add(value: str, kind: str) -> None:
        key = normalize(value)
        if len(key) >= MIN_LENGTH:
            needles.setdefault(key, kind)

    for column in columns:
        index = original.headers.index(column)
        for row in original.rows:
            cell = row[index] if index < len(row) else None
            if cell is None or str(cell) == "":
                continue
            text = str(cell)
            add(text, f"{column} の値")
            folded = unicodedata.normalize("NFKC", text)
            if "@" in folded:
                add(folded.split("@", 1)[0], f"{column} の @ より前")
    return needles


def find_leaks(output: Table, needles: dict[str, str], skip_columns: set[str]) -> list[Leak]:
    leaks: list[Leak] = []
    for row_number, row in zip(output.row_numbers, output.rows):
        for column, cell in zip(output.headers, row):
            if column in skip_columns or not isinstance(cell, str):
                continue
            normalized = normalize(cell)
            kinds = [kind for needle, kind in needles.items() if needle in normalized]
            if _EMAIL.search(unicodedata.normalize("NFKC", cell)):
                kinds.append(EMAIL_KIND)
            for kind in dict.fromkeys(kinds):
                leaks.append(Leak(row_number, column, kind))
    return leaks
