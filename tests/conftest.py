import keyring
import keyring.backend
import keyring.errors
import pytest

MASTER_TEXT = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8"


class MemoryKeyring(keyring.backend.KeyringBackend):
    priority = 1

    def __init__(self):
        self._store = {}

    def get_password(self, service, username):
        return self._store.get((service, username))

    def set_password(self, service, username, password):
        self._store[(service, username)] = password

    def delete_password(self, service, username):
        self._store.pop((service, username), None)


class BrokenKeyring(keyring.backend.KeyringBackend):
    priority = 1

    def get_password(self, service, username):
        raise keyring.errors.NoKeyringError()

    def set_password(self, service, username, password):
        raise keyring.errors.NoKeyringError()

    def delete_password(self, service, username):
        raise keyring.errors.NoKeyringError()


@pytest.fixture(autouse=True)
def fake_keyring():
    original = keyring.get_keyring()
    backend = MemoryKeyring()
    keyring.set_keyring(backend)
    yield backend
    keyring.set_keyring(original)


@pytest.fixture
def broken_keyring(fake_keyring):
    keyring.set_keyring(BrokenKeyring())
    yield
    keyring.set_keyring(fake_keyring)


FORMS_HEADERS = {
    "en": ["ID", "Start time", "Completion time", "Email", "Name", "Q1"],
    "ja": ["ID", "開始時刻", "完了時刻", "メール", "名前", "Q1"],
}


def _default_forms_rows():
    from datetime import datetime

    return [
        [1, datetime(2026, 10, 1, 9, 0, 0), datetime(2026, 10, 1, 9, 5, 0),
         "taro@example.com", "架空 太郎", "回答A"],
        [2, datetime(2026, 10, 1, 10, 0, 0), datetime(2026, 10, 1, 10, 4, 0),
         "Hanako@Example.com", "Hanako Example", "回答B"],
        [3, datetime(2026, 10, 8, 9, 1, 0), datetime(2026, 10, 8, 9, 6, 0),
         "taro@example.com", "架空 太郎", "回答C"],
    ]


@pytest.fixture
def forms_xlsx(tmp_path):
    from openpyxl import Workbook

    def make(lang="en", rows=None, name="forms.xlsx", directory=None):
        wb = Workbook()
        ws = wb.active
        ws.append(FORMS_HEADERS[lang])
        for row in rows if rows is not None else _default_forms_rows():
            ws.append(row)
        path = (directory or tmp_path) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        wb.save(path)
        return path

    return make


@pytest.fixture
def forms_csv(tmp_path):
    import csv

    def make(lang="en", rows=None, name="forms.csv", directory=None):
        path = (directory or tmp_path) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(FORMS_HEADERS[lang])
            for row in rows if rows is not None else _default_forms_rows():
                w.writerow([c.isoformat(sep=" ") if hasattr(c, "isoformat") else c for c in row])
        return path

    return make


@pytest.fixture
def master_key(fake_keyring):
    fake_keyring.set_password("jtk", "master", MASTER_TEXT)
