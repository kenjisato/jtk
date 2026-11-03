from conftest import MASTER_TEXT
from typer.testing import CliRunner

from jtk.cli import app
from jtk.crypto import decode_master_key, encode_master_key, fingerprint

runner = CliRunner()


def test_new_stores_and_shows_key_once(fake_keyring):
    r = runner.invoke(app, ["key", "new"])
    assert r.exit_code == 0
    stored = fake_keyring.get_password("jtk", "master")
    assert stored in r.output and "Bitwarden" in r.output and "jtk key set" in r.output
    assert fingerprint(decode_master_key(stored)) in r.output


def test_new_refuses_when_key_exists(fake_keyring):
    runner.invoke(app, ["key", "new"])
    before = fake_keyring.get_password("jtk", "master")
    r = runner.invoke(app, ["key", "new"])
    assert r.exit_code == 1 and before not in r.output
    assert fake_keyring.get_password("jtk", "master") == before


def test_set_reads_hidden_input(fake_keyring):
    r = runner.invoke(app, ["key", "set"], input=MASTER_TEXT + "\n")
    assert r.exit_code == 0 and MASTER_TEXT not in r.output
    assert fake_keyring.get_password("jtk", "master") == MASTER_TEXT


def test_set_rejects_bad_format(fake_keyring):
    r = runner.invoke(app, ["key", "set"], input="not-a-key\n")
    assert r.exit_code == 1 and fake_keyring.get_password("jtk", "master") is None


def test_set_requires_force_when_key_exists(fake_keyring):
    fake_keyring.set_password("jtk", "master", MASTER_TEXT)
    other = encode_master_key(bytes(32))
    assert runner.invoke(app, ["key", "set"], input=other + "\n").exit_code == 1
    assert runner.invoke(app, ["key", "set", "--force"], input=other + "\n").exit_code == 0
    assert fake_keyring.get_password("jtk", "master") == other


def test_fingerprint(fake_keyring):
    fake_keyring.set_password("jtk", "master", MASTER_TEXT)
    r = runner.invoke(app, ["key", "fingerprint"])
    assert r.exit_code == 0 and "312e56e9" in r.output and MASTER_TEXT not in r.output


def test_fingerprint_without_key_shows_steps(fake_keyring):
    r = runner.invoke(app, ["key", "fingerprint"])
    assert r.exit_code == 1 and "jtk key set" in r.output


def test_broken_keyring_shows_steps(broken_keyring):
    r = runner.invoke(app, ["key", "fingerprint"])
    assert r.exit_code == 1 and "jtk key set" in r.output


def test_pretty_exceptions_disabled():
    import jtk.cli
    import jtk.keys

    for a in (jtk.cli.app, jtk.keys.app):
        assert a.pretty_exceptions_enable is False
