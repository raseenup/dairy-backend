"""Generate a compatible reviewer password hash locally; never commit the output."""
import base64
import getpass
import hashlib
import re
import secrets

def main():
    password = getpass.getpass("New reviewer password: ")
    if not 10 <= len(password) <= 128 or not all(re.search(p, password) for p in (r"[A-Z]", r"[a-z]", r"[0-9]", r"[^A-Za-z0-9\s]")):
        raise SystemExit("Password does not meet FMV password policy.")
    if password != getpass.getpass("Confirm password: "):
        raise SystemExit("Passwords do not match.")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=5, dklen=32)
    print("scrypt-v2$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode())

if __name__ == "__main__":
    main()
