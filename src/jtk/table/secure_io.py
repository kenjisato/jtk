"""パスワード付き xlsx の読み書き。平文は BytesIO の中だけに置く。"""

import os
import tempfile
from io import BytesIO
from pathlib import Path

import msoffcrypto
from msoffcrypto.exceptions import DecryptionError, InvalidKeyError
from msoffcrypto.format.ooxml import OOXMLFile
from openpyxl import load_workbook

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


def read_encrypted_xlsx(path: Path, password: str) -> Table:
    path = Path(path)
    try:
        plain = _decrypt(path, password)
        wb = load_workbook(plain, data_only=True)
        try:
            ws = wb.worksheets[0]
            table = _build(enumerate(ws.iter_rows(values_only=True), start=1))
            table.sheet_title = ws.title
        finally:
            wb.close()
        return table
    except JtkError:
        raise
    except Exception:
        # 例外の本文にセルの値が入ることがあるので連結しない
        raise JtkError(f"{path.name} を表として読めません") from None


def write_encrypted_xlsx(table: Table, path: Path, password: str) -> None:
    path = Path(path)
    tmp_name = None
    try:
        plain = BytesIO()
        _write_xlsx(table, plain)
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
