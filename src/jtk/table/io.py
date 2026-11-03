import csv
from pathlib import Path

from openpyxl import Workbook, load_workbook

from jtk.errors import JtkError
from jtk.table import Table

SUPPORTED_SUFFIXES = (".xlsx", ".csv")


def _suffix(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise JtkError(
            f"{path.name} は対応していない形式です（対応：{', '.join(SUPPORTED_SUFFIXES)}）"
        )
    return suffix


def _is_blank(row) -> bool:
    return all(c is None or (isinstance(c, str) and not c.strip()) for c in row)


def _build(records) -> Table:
    """(行番号, 値のリスト) の列から Table を作る。最初の組が見出し。"""
    it = iter(records)
    try:
        _, head = next(it)
    except StopIteration:
        raise JtkError("見出し行がありません") from None
    headers = ["" if h is None else str(h) for h in head]
    rows, numbers = [], []
    for number, row in it:
        if _is_blank(row):
            continue
        row = list(row)
        if len(row) > len(headers):
            raise JtkError(f"{number} 行目の列数が見出しより多くなっています")
        row += [""] * (len(headers) - len(row))
        rows.append(row)
        numbers.append(number)
    return Table(headers, rows, numbers)


def _read_csv(path: Path) -> tuple[Table, list[str]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        table = _build(enumerate(csv.reader(f), start=1))
    return table, []


def _read_xlsx(path: Path) -> tuple[Table, list[str]]:
    wb = load_workbook(path, data_only=True)
    try:
        ws = wb.worksheets[0]
        warnings = []
        if len(wb.worksheets) > 1:
            warnings.append(
                f"シートが {len(wb.worksheets)} 枚あります。"
                f"最初のシート「{ws.title}」だけを読みます"
            )
        table = _build(enumerate(ws.iter_rows(values_only=True), start=1))
        table.sheet_title = ws.title
    finally:
        wb.close()
    return table, warnings


def read_table(path: Path) -> tuple[Table, list[str]]:
    path = Path(path)
    suffix = _suffix(path)
    try:
        return _read_csv(path) if suffix == ".csv" else _read_xlsx(path)
    except JtkError:
        raise
    except Exception:
        # 例外の本文にセルの値が入ることがあるので連結しない
        raise JtkError(f"{path.name} を表として読めません") from None


def _write_csv(table: Table, path: Path) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(table.headers)
        w.writerows(table.rows)


def _write_xlsx(table: Table, path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    if table.sheet_title is not None:
        ws.title = table.sheet_title
    for r, row in enumerate([table.headers, *table.rows], start=1):
        for c, value in enumerate(row, start=1):
            cell = ws.cell(row=r, column=c, value=value)
            if isinstance(value, str):
                cell.data_type = "s"  # "=" で始まる文字列も数式にしない
    wb.save(path)


def write_table(table: Table, path: Path) -> None:
    path = Path(path)
    suffix = _suffix(path)
    try:
        if suffix == ".csv":
            _write_csv(table, path)
        else:
            _write_xlsx(table, path)
    except Exception:
        # 例外の本文にセルの値が入ることがあるので連結しない
        raise JtkError(f"{path.name} に書き込めません") from None
