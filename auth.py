
import base64
import hashlib
import hmac
import os
import secrets
import time
from pathlib import Path

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from models import Admin, User



_N, _R, _P = 2**14, 8, 1


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P)
    return "$".join(["scrypt", str(_N), str(_R), str(_P),
                     base64.b64encode(salt).decode(), base64.b64encode(digest).decode()])


def verify_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, digest = stored.split("$")
        actual = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt),
                                n=int(n), r=int(r), p=int(p))
        return hmac.compare_digest(actual, base64.b64decode(digest))
    except Exception:
        return False


def generate_temp_password(length: int = 12) -> str:
    """Readable temporary password (no look-alike characters), always letters and digits."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789"
    while True:
        pw = "".join(secrets.choice(alphabet) for _ in range(length))
        if any(c.isdigit() for c in pw) and any(c.isalpha() for c in pw):
            return pw


# ------------------------------------------------------------------ lookups

def find_by_login(db: Session, login: str):
    """Find an admin or a user by username or email. Returns (role, account) or (None, None)."""
    for role, model in (("admin", Admin), ("user", User)):
        account = db.scalar(select(model).where(or_(model.username == login, model.email == login)))
        if account:
            return role, account
    return None, None


def taken_fields(db: Session, email: str, phone: str, username: str, exclude=None) -> set:
    """Which of email / phone / username already belong to any admin or user.
    `exclude` is an account whose own details should not count as taken."""
    taken = set()
    for model in (Admin, User):
        for field, value in (("email", email), ("phone", phone), ("username", username)):
            query = select(model.id).where(getattr(model, field) == value)
            if exclude is not None and isinstance(exclude, model):
                query = query.where(model.id != exclude.id)
            if db.scalar(query):
                taken.add(field)
    return taken


# ------------------------------------------------------------------ sessions

COOKIE_NAME = "karibu_session"
SESSION_TTL = 12 * 60 * 60            # normal login: 12 hours, ends when the browser closes
REMEMBER_TTL = 30 * 24 * 60 * 60      # "Remember me": 30 days


def _load_secret() -> bytes:
    if os.environ.get("KARIBU_SECRET"):
        return os.environ["KARIBU_SECRET"].encode()
    path = Path(__file__).resolve().parent / ".secret_key"
    if not path.exists():
        path.write_bytes(secrets.token_hex(32).encode())
        os.chmod(path, 0o600)
    return path.read_bytes().strip()


_SECRET = _load_secret()


def _sign(message: str) -> str:
    return hmac.new(_SECRET, message.encode(), hashlib.sha256).hexdigest()


def _pw_tag(account) -> str:
    return hashlib.sha256(account.password_hash.encode()).hexdigest()[:12]


def make_token(role: str, account, remember: bool) -> str:
    expires = int(time.time()) + (REMEMBER_TTL if remember else SESSION_TTL)
    message = f"{role}:{account.id}:{expires}:{_pw_tag(account)}"
    return f"{message}:{_sign(message)}"


def was_remembered(request) -> bool:
    """True if the current session came from a 'Remember me' login."""
    try:
        expires = int(request.cookies.get(COOKIE_NAME, "").split(":")[2])
    except (IndexError, ValueError):
        return False
    return expires - time.time() > SESSION_TTL


def cookie_max_age(remember: bool):
    return REMEMBER_TTL if remember else None  # None = ends when the browser closes


def current_account(request, db: Session):
    """Return (role, account) for the logged-in person, or (None, None)."""
    token = request.cookies.get(COOKIE_NAME)
    try:
        role, account_id, expires, tag, sig = token.split(":")
        if not hmac.compare_digest(sig, _sign(f"{role}:{account_id}:{expires}:{tag}")):
            return None, None
        if int(expires) < time.time():
            return None, None
        account = db.get({"admin": Admin, "user": User}[role], int(account_id))
    except (AttributeError, ValueError, KeyError):
        return None, None
    if not account or getattr(account, "is_active", True) is False:
        return None, None
    if not hmac.compare_digest(tag, _pw_tag(account)):
        return None, None
    return role, account