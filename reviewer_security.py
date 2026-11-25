"""Reviewer authentication primitives. No production state or external service access."""
import os
import re
import base64
import hashlib
import hmac
from copy import deepcopy

def enabled():
    return os.getenv("FMV_REVIEWER_ENABLED", "").lower() == "true"

def email():
    return os.getenv("FMV_REVIEWER_EMAIL", "").strip().lower()

def authenticate(identifier, password, portal):
    if not enabled() or portal != "customer" or not email() or identifier.strip().lower() != email():
        return False
    try:
        version, salt, digest = os.environ["FMV_REVIEWER_PASSWORD_HASH"].split("$")
        if version != "scrypt-v2" or not 10 <= len(password) <= 128:
            return False
        salt = base64.b64decode(salt, validate=True)
        digest = base64.b64decode(digest, validate=True)
        if len(salt) != 16 or len(digest) != 32:
            return False
        actual = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=5, dklen=32)
        return hmac.compare_digest(actual, digest)
    except (KeyError, ValueError, TypeError):
        return False

def isolated_state(initial_state):
    """Create synthetic state only. Never copy live customer or business records."""
    state = deepcopy(initial_state())
    identity = email()
    if not identity:
        raise ValueError("Reviewer email not configured")
    state["users"]["customer:" + identity] = {
        "id": "USR-PLAY-REVIEWER", "email": identity, "phone": "",
        "full_name": "Google Play Reviewer", "address": "Demo address",
        "portal": "customer", "role": "customer", "approved": True,
        "token_version": 0, "password_hash": "", "reviewer": True,
    }
    return state
