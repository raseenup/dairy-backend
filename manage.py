from dotenv import load_dotenv
load_dotenv()

"""One-time staff invitation creation on the trusted backend host."""
import argparse
import hashlib
import secrets
import time
from secure_backend import transaction, ROLES
from pydantic import TypeAdapter, EmailStr

if __name__=='__main__':
    p=argparse.ArgumentParser(description='Create a 24-hour named administrator invitation.')
    p.add_argument('email');p.add_argument('--role',choices=sorted(ROLES),default='super_admin')
    a=p.parse_args();email=str(TypeAdapter(EmailStr).validate_python(a.email)).lower();token=secrets.token_urlsafe(32)
    with transaction() as s:
        s['invites'][hashlib.sha256(token.encode()).hexdigest()]={'email':email,'role':a.role,'expires':time.time()+86400,'used':False}
    print('Private one-use invitation for',email,'role',a.role,':',token)
