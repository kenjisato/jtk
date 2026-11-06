import zipfile
from pathlib import Path

import pytest

from jtk.errors import JtkError
from jtk.table import Table
from jtk.table.io import write_table
from jtk.table.secure_io import WrongPassword, read_encrypted_xlsx, write_encrypted_xlsx


def test_roundtrip(tmp_path):
    t = Table(["学年", "姓"], [["B25", "架空"]], [2])
    p = tmp_path / "r.xlsx"
    write_encrypted_xlsx(t, p, "pw")
    assert read_encrypted_xlsx(p, "pw").rows == [["B25", "架空"]]


def test_file_is_not_plain_zip(tmp_path):
    p = tmp_path / "r.xlsx"
    write_encrypted_xlsx(Table(["姓"], [["架空"]], [2]), p, "pw")
    assert not zipfile.is_zipfile(p)  # 暗号化された xlsx は OLE 形式
    assert "架空".encode() not in p.read_bytes()


def test_wrong_password(tmp_path):
    p = tmp_path / "r.xlsx"
    write_encrypted_xlsx(Table(["姓"], [["架空"]], [2]), p, "pw")
    with pytest.raises(WrongPassword, match="パスワードが違います"):
        read_encrypted_xlsx(p, "nope")


def test_plain_xlsx_is_rejected(tmp_path):
    # パスワードなしの xlsx は対応表として受け付けない
    p = tmp_path / "plain.xlsx"
    write_table(Table(["姓"], [["架空"]], [2]), p)
    with pytest.raises(JtkError, match="パスワードがかかっていません"):
        read_encrypted_xlsx(p, "pw")


def test_failed_write_leaves_no_temp_file(tmp_path, monkeypatch):
    import msoffcrypto.format.ooxml as ooxml

    def boom(self, password, out):
        out.write(b"partial")
        raise RuntimeError("架空")

    monkeypatch.setattr(ooxml.OOXMLFile, "encrypt", boom)
    p = tmp_path / "r.xlsx"
    with pytest.raises(JtkError) as e:
        write_encrypted_xlsx(Table(["姓"], [["架空"]], [2]), p, "pw")
    assert "架空" not in str(e.value)
    assert list(tmp_path.iterdir()) == []


def test_failed_write_keeps_existing_file(tmp_path, monkeypatch):
    p = tmp_path / "r.xlsx"
    write_encrypted_xlsx(Table(["姓"], [["架空"]], [2]), p, "pw")
    before = p.read_bytes()
    import msoffcrypto.format.ooxml as ooxml

    monkeypatch.setattr(ooxml.OOXMLFile, "encrypt", lambda s, pw, out: 1 / 0)
    with pytest.raises(JtkError):
        write_encrypted_xlsx(Table(["姓"], [["別"]], [2]), p, "pw")
    assert p.read_bytes() == before
    assert [x.name for x in tmp_path.iterdir()] == ["r.xlsx"]
