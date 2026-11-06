"""パスワード付き xlsx の読み書き。平文は BytesIO の中だけに置く。"""

import os
import tempfile
from collections.abc import Callable
from io import BytesIO
from pathlib import Path

import msoffcrypto
from msoffcrypto.exceptions import DecryptionError, InvalidKeyError
from msoffcrypto.format.ooxml import OOXMLFile
from openpyxl import Workbook, load_workbook

from jtk.errors import JtkError
from jtk.table import Table
from jtk.table.io import _build, _write_xlsx


class WrongPassword(JtkError):
    def __init__(self) -> None:
        super().__init__("パスワードが違います")


def _decrypt(path: Path, password: str) -> BytesIO:
    with path.open("rb") as fp:
        office = msoffcrypto.OfficeFile(fp)
        if not office.is_encrypted():
            raise JtkError(f"{path.name} にパスワードがかかっていません")
        try:
            office.load_key(password=password)
            plain = BytesIO()
            office.decrypt(plain)
        except (InvalidKeyError, DecryptionError):
            raise WrongPassword() from None
    plain.seek(0)
    return plain


def _first_sheet_table(wb: Workbook) -> Table:
    ws = wb.worksheets[0]
    table = _build(enumerate(ws.iter_rows(values_only=True), start=1))
    table.sheet_title = ws.title
    return table


def read_encrypted_xlsx(path: Path, password: str) -> Table:
    path = Path(path)
    try:
        plain = _decrypt(path, password)
        wb = load_workbook(plain, data_only=True)
        try:
            return _first_sheet_table(wb)
        finally:
            wb.close()
    except JtkError:
        raise
    except Exception:
        # 例外の本文にセルの値が入ることがあるので連結しない
        raise JtkError(f"{path.name} を表として読めません") from None


def read_encrypted_workbook(path: Path, password: str) -> tuple[Table, Workbook]:
    """1 回だけ復号し、同じ平文から 2 回読む。

    表（最初のシート）は値で読み、ブックは数式を残したまま返す。ブックを書き換えて
    write_encrypted_workbook に渡せば、ほかのシートや書式はそのまま残る。
    """
    path = Path(path)
    try:
        data = _decrypt(path, password).getvalue()
        values = load_workbook(BytesIO(data), data_only=True)
        try:
            table = _first_sheet_table(values)
        finally:
            values.close()
        return table, load_workbook(BytesIO(data))
    except JtkError:
        raise
    except Exception:
        raise JtkError(f"{path.name} を表として読めません") from None


def write_encrypted_xlsx(table: Table, path: Path, password: str) -> None:
    _encrypt_to(path, password, lambda plain: _write_xlsx(table, plain))


def write_encrypted_workbook(wb: Workbook, path: Path, password: str) -> None:
    _encrypt_to(path, password, wb.save)


def _encrypt_to(path: Path, password: str, write_plain: Callable[[BytesIO], None]) -> None:
    """平文をメモリに書き、暗号化して同じフォルダの一時ファイルに書いてから置き換える。"""
    path = Path(path)
    tmp_name = None
    try:
        plain = BytesIO()
        write_plain(plain)
        plain.seek(0)
        fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
        with os.fdopen(fd, "wb") as out:
            OOXMLFile(plain).encrypt(password, out)
        os.replace(tmp_name, path)
        tmp_name = None
    except Exception:
        raise JtkError(f"{path.name} に書き込めません") from None
    finally:
        if tmp_name is not None:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
