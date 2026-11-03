"""親鍵・スコープ鍵・メールアドレスのハッシュ。標準ライブラリだけで作る。"""

import base64
import binascii
import hashlib
import hmac
import secrets
import unicodedata

from jtk.errors import JtkError

_KEY_BYTES = 32
_BAD_KEY = "親鍵の形式が正しくありません（32 バイトの base64url）"


def _hmac(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()


def generate_master_key() -> bytes:
    return secrets.token_bytes(_KEY_BYTES)


def encode_master_key(key: bytes) -> str:
    return base64.urlsafe_b64encode(key).decode("ascii").rstrip("=")


def decode_master_key(text: str) -> bytes:
    body = text.strip().rstrip("=")
    try:
        key = base64.b64decode(
            body + "=" * (-len(body) % 4), altchars=b"-_", validate=True
        )
    except (binascii.Error, ValueError):
        raise JtkError(_BAD_KEY) from None
    if len(key) != _KEY_BYTES:
        raise JtkError(_BAD_KEY)
    return key


def derive_scope_key(master: bytes, scope: str) -> bytes:
    scope = unicodedata.normalize("NFC", scope).strip()
    if not scope:
        raise JtkError("スコープが空です")
    return _hmac(master, "jtk:pii-hash:v1:" + scope)


def normalize_email(email: str) -> str:
    return unicodedata.normalize("NFKC", email).strip().lower()


def hash_email(scope_key: bytes, email: str) -> str:
    return _hmac(scope_key, normalize_email(email)).hex()[:16]


def fingerprint(master: bytes) -> str:
    return _hmac(master, "jtk:fingerprint").hex()[:8]
