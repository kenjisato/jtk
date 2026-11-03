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
