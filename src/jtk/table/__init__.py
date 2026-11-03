from dataclasses import dataclass


@dataclass
class Table:
    """見出しと行の表。row_numbers[i] は rows[i] の表計算ソフト上の行番号。"""

    headers: list[str]
    rows: list[list[object]]
    row_numbers: list[int]
    sheet_title: str | None = None
