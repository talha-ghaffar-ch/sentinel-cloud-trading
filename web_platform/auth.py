"""
auth.py — Authentication helpers for Sentinel Web Platform
"""

import os
import re
import uuid
import bcrypt
from functools import wraps
from flask import session, redirect, url_for, flash, abort


# ── Password hashing ──────────────────────────────────────────
def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt(rounds=12)).decode()

def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except Exception:
        return False


# ── User ID generation ────────────────────────────────────────
def generate_user_id(email: str) -> str:
    """
    Generate a short, deterministic-looking user_id from email.
    Format: user_XXXX where XXXX is a unique suffix.
    Must match the --user arg format expected by Trading_Engine.
    """
    import hashlib
    h = hashlib.md5(email.encode()).hexdigest()[:6]
    return f"user_{h}"


# ── Auth decorators ───────────────────────────────────────────
def login_required(f):
    """Redirect to login if user not in session."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in to continue.", "warning")
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


def verified_required(f):
    """Require account_status == 'verified' (approved by admin)."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in to continue.", "warning")
            return redirect(url_for("login"))
        if session.get("account_status") != "verified":
            flash("Your account must be verified to access the trading terminal.", "info")
            return redirect(url_for("dashboard"))
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    """Require role == 'admin'."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        if session.get("role") != "admin":
            abort(403)
        return f(*args, **kwargs)
    return decorated


# ── Validation helpers ────────────────────────────────────────
def validate_email(email: str) -> bool:
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email))

def validate_password(pw: str) -> tuple[bool, str]:
    if len(pw) < 8:
        return False, "Password must be at least 8 characters."
    if not re.search(r"[A-Z]", pw):
        return False, "Password must contain at least one uppercase letter."
    if not re.search(r"[0-9]", pw):
        return False, "Password must contain at least one number."
    return True, ""
