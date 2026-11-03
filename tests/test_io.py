from datetime import datetime

import pytest
from openpyxl import Workbook, load_workbook
from openpyxl.styles import PatternFill

from jtk.errors import JtkError
from jtk.table.io import Table, read_table, write_table


def test_csv_roundtrip_keeps_strings(tmp_path):
    t = Table(["id", "v", "d"], [["007", "1.50", "2026-11-03"]], [2])
    p = tmp_path / "a.csv"
    write_table(t, p)
    back, warnings = read_table(p)
    assert back.headers == ["id", "v", "d"]
    assert back.rows == [["007", "1.50", "2026-11-03"]]
    assert warnings == []
    nobom = tmp_path / "b.csv"
    nobom.write_bytes("id,v\n007,1.50\n".encode("utf-8"))
    back, _ = read_table(nobom)
    assert back.headers == ["id", "v"]
    assert back.rows == [["007", "1.50"]]


def test_csv_written_with_bom(tmp_path):
    p = tmp_path / "a.csv"
    write_table(Table(["a"], [["x"]], [2]), p)
    assert p.read_bytes()[:3] == b"\xef\xbb\xbf"


def test_csv_short_row_padded_long_row_error(tmp_path):
    p = tmp_path / "a.csv"
    p.write_text("a,b,c\n1,2\n", encoding="utf-8")
    t, _ = read_table(p)
    assert t.rows == [["1", "2", ""]]
    p.write_text("a,b\n1,2,3\n", encoding="utf-8")
    with pytest.raises(JtkError) as e:
        read_table(p)
    assert "2" in str(e.value)


def test_xlsx_roundtrip_keeps_types(tmp_path):
    row = [datetime(2026, 11, 3, 9, 30), 12, 3.5, "文字"]
    t = Table(["d", "i", "f", "s"], [row], [2], sheet_title="名簿")
    p = tmp_path / "a.xlsx"
    write_table(t, p)
    back, warnings = read_table(p)
    assert back.rows == [row]
    assert [type(x) for x in back.rows[0]] == [datetime, int, float, str]
    assert back.sheet_title == "名簿"
    assert warnings == []


def test_xlsx_multiple_sheets_warns(tmp_path):
    wb = Workbook()
    ws = wb.active
    ws.title = "first"
    ws.append(["h"])
    ws.append(["a"])
    ws2 = wb.create_sheet("second")
    ws2.append(["h"])
    ws2.append(["b"])
    p = tmp_path / "a.xlsx"
    wb.save(p)
    t, warnings = read_table(p)
    assert t.rows == [["a"]]
    assert warnings == ["シートが 2 枚あります。最初のシート「first」だけを読みます"]


def test_blank_rows_skipped_and_row_numbers_kept(tmp_path):
    wb = Workbook()
    ws = wb.active
    ws.append(["h1", "h2"])
    ws.append(["a", 1])
    ws.append([None, "  "])
    ws.append(["b", 2])
    ws["A6"].fill = PatternFill("solid", fgColor="FFFF00")
    ws["A6"].number_format = "0.00"
    p = tmp_path / "a.xlsx"
    wb.save(p)
    assert load_workbook(p).active.max_row == 6
    t, _ = read_table(p)
    assert t.rows == [["a", 1], ["b", 2]]
    assert t.row_numbers == [2, 4]

    c = tmp_path / "a.csv"
    c.write_text("h\na\n\n,\nb\n", encoding="utf-8")
    t, _ = read_table(c)
    assert t.rows == [["a"], ["b"]]
    assert t.row_numbers == [2, 5]


@pytest.mark.parametrize("name", ["a.xls", "a.tsv"])
def test_unsupported_suffix(tmp_path, name):
    p = tmp_path / name
    p.write_text("x")
    with pytest.raises(JtkError):
        read_table(p)
    with pytest.raises(JtkError):
        write_table(Table(["a"], [], []), p)


def test_broken_xlsx_message_has_no_content(tmp_path):
    p = tmp_path / "a.xlsx"
    p.write_text("secret-value")
    with pytest.raises(JtkError) as e:
        read_table(p)
    assert "secret-value" not in str(e.value)
    assert "a.xlsx" in str(e.value)
