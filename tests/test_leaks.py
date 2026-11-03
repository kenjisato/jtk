from jtk.table import Table
from jtk.table.leaks import EMAIL_KIND, Leak, collect_needles, find_leaks
from jtk.table.pii import Roles


def table(headers, rows):
    return Table(headers, rows, list(range(2, 2 + len(rows))))


def test_name_of_other_row_in_free_text():
    original = table(["Name", "Q1"], [["架空 太郎", "a"], ["架空 花子", "b"]])
    needles = collect_needles(original, Roles(None, ["Name"], []))
    output = table(["Q1"], [["a"], ["架空太郎さんと組みました"]])
    assert find_leaks(output, needles, set()) == [Leak(3, "Q1", "Name の値")]


def test_fullwidth_and_case_insensitive():
    original = table(["Name"], [["Alice Example"]])
    needles = collect_needles(original, Roles(None, ["Name"], []))
    output = table(["Q1"], [["ＡＬＩＣＥ　example"]])
    assert find_leaks(output, needles, set()) == [Leak(2, "Q1", "Name の値")]


def test_email_local_part():
    original = table(["Email"], [["s1234567@example.jp"]])
    needles = collect_needles(original, Roles("Email", [], []))
    output = table(["Q1"], [["番号は s1234567 です"]])
    assert find_leaks(output, needles, set()) == [Leak(2, "Q1", "Email の @ より前")]


def test_digits_only_needle_is_searched():
    original = table(["Email"], [["12345678@example.jp"]])
    needles = collect_needles(original, Roles("Email", [], []))
    output = table(["Q1"], [["学籍番号 12345678"]])
    assert find_leaks(output, needles, set()) == [Leak(2, "Q1", "Email の @ より前")]


def test_pii_email_column_in_drop_mode():
    original = table(["Email", "Name"], [["s1234567@example.jp", "架空 太郎"]])
    needles = collect_needles(original, Roles(None, ["Email", "Name"], []))
    output = table(["Q1"], [["s1234567@example.jp"]])
    kinds = {leak.kind for leak in find_leaks(output, needles, set())}
    assert {"Email の値", "Email の @ より前", EMAIL_KIND} <= kinds


def test_short_needle_skipped():
    original = table(["Name"], [["李"]])
    needles = collect_needles(original, Roles(None, ["Name"], []))
    assert needles == {}
    assert find_leaks(table(["Q1"], [["李"]]), needles, set()) == []


def test_email_shape():
    output = table(["Q1"], [["連絡先 ｘ＠example.org"]])
    assert find_leaks(output, {}, set()) == [Leak(2, "Q1", EMAIL_KIND)]


def test_non_string_cells_ignored():
    output = table(["Q1"], [[2026]])
    assert find_leaks(output, {"2026": "Name の値"}, set()) == []


def test_hash_column_skipped():
    output = table(["Email", "Q1"], [["s1234567", "x"]])
    needles = {"s1234567": "Email の @ より前"}
    assert find_leaks(output, needles, {"Email"}) == []


def test_clean_table_has_no_leaks():
    original = table(["Name", "Q1"], [["架空 太郎", "a"]])
    needles = collect_needles(original, Roles(None, ["Name"], []))
    assert find_leaks(table(["Q1"], [["特になし"]]), needles, set()) == []


def test_one_leak_per_cell_and_kind():
    needles = {"taro1234": "Email の @ より前"}
    output = table(["Q1"], [["taro1234 taro1234"]])
    assert len(find_leaks(output, needles, set())) == 1
