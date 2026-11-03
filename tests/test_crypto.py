import pytest

from jtk.crypto import (
    decode_master_key,
    derive_scope_key,
    encode_master_key,
    fingerprint,
    generate_master_key,
    hash_email,
)
from jtk.errors import JtkError

MASTER = bytes(range(32))
MASTER_TEXT = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8"
SCOPE = "2026後期/経済数学I"


def test_encode_decode_roundtrip():
    assert encode_master_key(MASTER) == MASTER_TEXT
    assert decode_master_key(MASTER_TEXT) == MASTER
    assert decode_master_key(MASTER_TEXT + "=") == MASTER
    assert decode_master_key("  " + MASTER_TEXT + "\n") == MASTER


@pytest.mark.parametrize("bad", ["", "abc", MASTER_TEXT[:-4], "!!!" + MASTER_TEXT[3:]])
def test_decode_rejects_bad_key(bad):
    with pytest.raises(JtkError) as e:
        decode_master_key(bad)
    assert bad.strip() == "" or bad not in str(e.value)


def test_hash_email_known_answer():
    sk = derive_scope_key(MASTER, SCOPE)
    assert hash_email(sk, "taro@example.com") == "a4cd517475a2aba2"
    assert hash_email(sk, " Taro@Example.com ") == "a4cd517475a2aba2"
    assert hash_email(sk, "ｔａｒｏ＠example.com") == "a4cd517475a2aba2"  # NFKC


def test_scope_is_nfc_and_stripped():
    assert derive_scope_key(MASTER, f" {SCOPE} ") == derive_scope_key(MASTER, SCOPE)


def test_other_scope_differs():
    sk = derive_scope_key(MASTER, "2027前期/経済数学I")
    assert hash_email(sk, "taro@example.com") == "c81c82a06bed50db"


def test_empty_scope_rejected():
    with pytest.raises(JtkError):
        derive_scope_key(MASTER, "  ")


def test_fingerprint_known_answer():
    assert fingerprint(MASTER) == "312e56e9"


def test_generate_master_key_is_32_random_bytes():
    a, b = generate_master_key(), generate_master_key()
    assert len(a) == 32 and a != b
