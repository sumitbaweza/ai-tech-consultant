"""Passwords for the screens' sign-in, hashed with scrypt from Python's standard library.

BRAIN_UI_USERS in .env lists who may sign in, comma-separated, each as name:salt:hash, so .env
never holds a password. To add someone:

    uv run python -m ui.passwords sam      # asks for a password, prints the entry for .env
"""

import getpass
import hashlib
import hmac
import secrets
import sys

from app.config import get_settings

# scrypt's cost: 2^14 rounds over 8 blocks of 1 KiB, the minimum OWASP recommends. Slow enough
# to make guessing expensive, fast enough (about 50 ms) for a sign-in.
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1


def hash_password(password: str) -> str:
    """A fresh random salt and the scrypt hash, as salt:hash in hex."""
    salt = secrets.token_bytes(16)
    return f"{salt.hex()}:{_scrypt(password, salt).hex()}"


def check_password(password: str, stored: str) -> bool:
    salt_hex, _, hash_hex = stored.partition(":")
    # compare_digest takes as long however much matches, so the timing gives nothing away
    return hmac.compare_digest(_scrypt(password, bytes.fromhex(salt_hex)), bytes.fromhex(hash_hex))


def users() -> dict[str, str]:
    """Each user's name and salt:hash, from BRAIN_UI_USERS."""
    raw = get_settings().brain_ui_users.get_secret_value()
    entries = [entry.strip() for entry in raw.split(",") if entry.strip()]
    return {name: stored for name, _, stored in (entry.partition(":") for entry in entries)}


def sign_in(name: str, password: str) -> bool:
    """Whether the name and password match a user. An unknown name costs one scrypt too, so
    the time a sign-in takes doesn't tell which names exist."""
    stored = users().get(name)
    matches = check_password(password, stored or _NOBODY)
    return stored is not None and matches


def _scrypt(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P)


# What an unknown name is checked against: a random password nobody knows
_NOBODY = hash_password(secrets.token_hex(16))


if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else input("Name: ")
    password = getpass.getpass(f"Password for {name}: ")
    print(f"Add this to BRAIN_UI_USERS in .env, comma-separated: {name}:{hash_password(password)}")
