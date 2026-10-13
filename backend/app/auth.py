import hashlib, hmac, os
from dataclasses import dataclass
from typing import Optional
from .config import settings

_ITER = 200_000

@dataclass
class AuthResult:
    ok: bool
    name: Optional[str] = None
    role: Optional[str] = None
    error: Optional[str] = None

def hash_password(password: str) -> str:
    salt = os.urandom(16)
    d = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITER)
    return f"pbkdf2${salt.hex()}${d.hex()}"

def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt_hex, digest_hex = stored.split("$")
        if scheme != "pbkdf2":
            return False
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITER)
        return hmac.compare_digest(actual, expected)
    except (ValueError, AttributeError):
        return False

def authenticate(name: str, password: str) -> AuthResult:
    for user in settings.get_users():
        if user.get("name") == name:
            if verify_password(password, user.get("password", "")):
                return AuthResult(ok=True, name=user["name"], role=user.get("role", "user"))
            return AuthResult(ok=False, error="Incorrect password")
    return AuthResult(ok=False, error="Account not found")

def is_admin(role: str) -> bool:
    return role == "admin"
