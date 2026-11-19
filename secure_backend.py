"""Active application layer, v8. Original main.py schemas and source are retained.
SQLite transactions are the source of truth; no browser or in-memory success fallback.
"""
from __future__ import annotations
import asyncio
import base64
import hashlib
import hmac
import inspect
import io
import json
import math
import os
import re
import secrets
import sqlite3
import time
import threading
from contextlib import contextmanager
from datetime import datetime, date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Literal, Optional
from zoneinfo import ZoneInfo
import httpx
import jwt
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, Response, FileResponse
from fastapi.routing import APIRoute
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, EmailStr, Field, ConfigDict, TypeAdapter
from PIL import Image
from email_templates import build_email
from mail_routing import sender_for

IST = ZoneInfo('Asia/Kolkata')
ROOT = Path(__file__).resolve().parent
DATABASE_URL = os.getenv('DATABASE_URL', '')
DB_PATH = Path(os.getenv('FMV_DB_PATH', str(ROOT / 'data' / 'firstmilk.sqlite3')))

DB_PATH.parent.mkdir(parents=True, exist_ok=True)
SECRET = os.getenv('JWT_SECRET', '')
if len(SECRET) < 32 or SECRET.startswith(('REPLACE_', 'CHANGE_')) or SECRET == 'super-secret-jwt-key-firstmilk-2026':
    raise RuntimeError('Set JWT_SECRET to a unique random value of at least 32 characters before starting.')
PRODUCTION = os.getenv('APP_ENV', 'development') == 'production'
if PRODUCTION and not DATABASE_URL and os.getenv('ALLOW_PRODUCTION_SQLITE') != 'true':
    raise RuntimeError('Production requires DATABASE_URL (PostgreSQL), or explicit ALLOW_PRODUCTION_SQLITE=true with a persistent disk.')
SESSION_SECONDS = 1800
ACCOUNT_EXISTS_MESSAGE = 'An account with this email or mobile number already exists in this portal. Sign in or reset your password.'
ROLES = {'super_admin', 'operations_manager', 'finance', 'customer_support', 'content_manager'}
PERMISSIONS = {
    'super_admin': {'*'},
    'operations_manager': {'catalog','orders','drivers','logistics'},
    'finance': {'finance'},
    'customer_support': {'support'},
    'content_manager': {'content'},
}

def now(): return datetime.now(IST).isoformat()
def today(): return datetime.now(IST).date().isoformat()
def uid(prefix): return prefix + '-' + secrets.token_hex(10)
def require(ok, message='Access denied.', status=400):
    if not ok: raise HTTPException(status, message)

def money(value):
    try:
        d = Decimal(str(value))
        require(d.is_finite() and d >= 0 and d <= Decimal('100000000'), 'Invalid amount.')
        return int((d * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    except (ValueError, TypeError, ArithmeticError): raise HTTPException(422, 'Invalid amount.')

def password_hash(password):
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=5, dklen=32)
    return 'scrypt-v2$' + base64.b64encode(salt).decode() + '$' + base64.b64encode(derived).decode()

def password_ok(password, encoded):
    try:
        version, salt, expected = encoded.split('$')
        if version not in {'scrypt','scrypt-v2'}:return False
        actual = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=16384, r=8, p=5 if version=='scrypt-v2' else 1, dklen=32)
        return hmac.compare_digest(actual, base64.b64decode(expected))
    except (ValueError, TypeError): return False

DUMMY_HASH = password_hash(secrets.token_urlsafe(24))

def valid_password(password):
    # Enforce new passwords at sign-up and reset, not legacy-account sign-in.
    require(isinstance(password, str) and 10 <= len(password) <= 128
            and re.search('[A-Z]', password) and re.search('[a-z]', password)
            and re.search('[0-9]', password) and re.search(r'[^A-Za-z0-9\\s]', password),
            'Use 10–128 characters with at least one uppercase letter, one lowercase letter, one digit and one special character.', 422)

def phone_india(phone):
    value = re.sub(r'[\s()-]', '', phone)
    if value.startswith('+91'): value = value[3:]
    elif len(value) == 12 and value.startswith('91'): value = value[2:]
    require(bool(re.fullmatch('[6-9][0-9]{9}', value)), 'Enter a valid Indian mobile number.', 422)
    return '+91' + value

def clean_text(value, limit=3000):
    value = str(value or '').strip()
    require(len(value) <= limit and not any(ord(c) < 32 and c not in '\n\t' for c in value), 'Invalid text.', 422)
    return value

def public_user(u):
    return {k: u.get(k, '') for k in ('id','email','phone','full_name','address','portal','role','approved','content_editor','driver_manager','payout_approver')}

def initial_state():
    # Catalog retained from the supplied project; no fabricated users, orders or payments.
    products = [
        dict(id='prod-1',name='Pure Cow Ghee',description='Farm fresh dairy from Kerala.',price=350.0,unit='500g',stock_quantity=100),
        dict(id='prod-2',name='Fresh Curd',description='Farm fresh dairy from Kerala.',price=40.0,unit='500g',stock_quantity=120),
        dict(id='prod-3',name='Unsalted Butter',description='Farm fresh dairy from Kerala.',price=120.0,unit='250g',stock_quantity=80),
        dict(id='prod-4',name='Farm Fresh Milk',description='Daily fresh milk from Kerala farms.',price=60.0,unit='1 Liter',stock_quantity=250)]
    return dict(users={}, challenges={}, sessions={}, products={p['id']:dict(p,currency='INR',min_stock_threshold=20,version=1,tax_rate=0) for p in products},
                orders={}, subscriptions={}, drivers={}, shifts={}, expenses={}, sos={}, tickets={}, forum={}, votes={},
                team={}, profiles={}, quotes={}, rma={}, invoices={}, documents={}, uploads={}, notifications={}, surveys={},
                credit={}, branding={}, audit=[], idempotency={}, events={}, invites={}, revision=0,
                tax={'gstin':'','vat_rate':0,'zatca_tin':'','zatca_mode':'india_gst','country':'India','state':'Kerala'})

_pool = None
_pool_lock = threading.Lock()

def postgres_root_certificate(dsn):
    """Choose a trust store once when opening the production connection pool."""
    configured = os.getenv('PGSSLROOTCERT')
    if configured:
        return configured

    from psycopg import ProgrammingError
    from psycopg.conninfo import conninfo_to_dict
    try:
        options = conninfo_to_dict(dsn)
    except ProgrammingError:
        raise RuntimeError('Invalid PostgreSQL connection settings. Check DATABASE_URL.') from None
    if options.get('sslrootcert'):
        return options['sslrootcert']

    hosts = (options.get('host') or os.getenv('PGHOST', '')).split(',')
    pooler_host = r'[a-z0-9-]+\.pooler\.supabase\.com\.?'
    if all(re.fullmatch(pooler_host, host.strip().lower()) for host in hosts):
        return str(ROOT / 'certs' / 'supabase-production-roots.pem')
    return 'system'


def postgres_connect():
    """Reuse verified TLS connections across short transactions on free hosting."""
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                from psycopg_pool import ConnectionPool
                kwargs = dict(connect_timeout=10, sslmode='verify-full' if PRODUCTION else 'prefer',
                    sslrootcert=postgres_root_certificate(DATABASE_URL) if PRODUCTION else None,
                    prepare_threshold=None)
                _pool = ConnectionPool(DATABASE_URL, min_size=0, max_size=4, timeout=10,
                    kwargs=kwargs, check=ConnectionPool.check_connection, open=True)
    return _pool.connection()

@contextmanager
def postgres_transaction():
    with postgres_connect() as conn:
        # migration_v8_security.sql must be applied once by the database owner.
        with conn.cursor() as cur:
            lock_state = 'SELECT data FROM fmv_secure.application_state WHERE id=1 FOR UPDATE'
            cur.execute(lock_state)
            row = cur.fetchone()
            if row is None:
                # Concurrent first starts can both see an empty table. Lock and
                # reread the winning insert before using its committed state.
                cur.execute("INSERT INTO fmv_secure.application_state (id,data) VALUES (1,%s::jsonb) ON CONFLICT (id) DO NOTHING",
                            (json.dumps(initial_state()),))
                cur.execute(lock_state)
                row = cur.fetchone()
            state = row[0]
            yield state
            cur.execute('UPDATE fmv_secure.application_state SET data=%s::jsonb WHERE id=1',(json.dumps(state,allow_nan=False),))


def postgres_rate_limit(bucket,limit,seconds):
    current=time.time()
    with postgres_connect() as conn:
        with conn.cursor() as cur:
            # One atomic key lookup/update instead of scanning expired buckets
            # before every live sync. Expired windows reset under the row lock.
            cur.execute('''INSERT INTO fmv_secure.rate_limits (bucket,until_epoch,hits) VALUES (%s,%s,1)
                ON CONFLICT (bucket) DO UPDATE SET
                hits=CASE WHEN fmv_secure.rate_limits.until_epoch<=%s THEN 1
                    ELSE LEAST(fmv_secure.rate_limits.hits+1,%s) END,
                until_epoch=CASE WHEN fmv_secure.rate_limits.until_epoch<=%s THEN EXCLUDED.until_epoch
                    ELSE fmv_secure.rate_limits.until_epoch END RETURNING hits''',
                (bucket,current+seconds,current,limit+1,current))
            hits=cur.fetchone()[0]
            if secrets.randbelow(256)==0:
                cur.execute('''DELETE FROM fmv_secure.rate_limits WHERE until_epoch<%s AND bucket IN
                    (SELECT bucket FROM fmv_secure.rate_limits WHERE until_epoch<%s
                     ORDER BY until_epoch LIMIT 128)''',(current,current))
    require(hits<=limit,'Too many attempts. Please try again later.',429)


@contextmanager
def transaction():
    if DATABASE_URL:
        with postgres_transaction() as state:
            yield state
        return
    c = sqlite3.connect(DB_PATH, timeout=30, isolation_level=None)
    try:
        c.execute('PRAGMA journal_mode=WAL')
        c.execute('PRAGMA busy_timeout=30000')
        c.execute('CREATE TABLE IF NOT EXISTS application_state (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL)')
        c.execute('CREATE TABLE IF NOT EXISTS rate_limits (bucket TEXT PRIMARY KEY, until REAL NOT NULL, hits INTEGER NOT NULL)')
        c.execute('BEGIN IMMEDIATE')
        row = c.execute('SELECT data FROM application_state WHERE id=1').fetchone()
        s = json.loads(row[0]) if row else initial_state()
        yield s
        c.execute('INSERT OR REPLACE INTO application_state VALUES (1,?)', (json.dumps(s,allow_nan=False),))
        c.commit()
    except BaseException:
        c.rollback()
        raise
    finally: c.close()


@contextmanager
def read_transaction(maintain=True):
    """A current committed snapshot: no FOR UPDATE, no state rewrite, no cache."""
    if DATABASE_URL:
        with postgres_connect() as conn:
            row=conn.execute('SELECT data FROM fmv_secure.application_state WHERE id=1').fetchone()
            state=row[0] if row else initial_state()
    else:
        with sqlite3.connect(DB_PATH, timeout=10) as conn:
            row=conn.execute('SELECT data FROM application_state WHERE id=1').fetchone()
            state=json.loads(row[0]) if row else initial_state()
    # Upgrade to a write transaction only when an actual reservation expires.
    if maintain and any(o['status']=='Awaiting Payment' and (o.get('reservation_expires') or 0)<time.time()
                        for o in state['orders'].values()):
        with transaction() as current:
            expire_reservations(current)
            state=current
    yield state


def challenge_request(request, body):
    """Hash passwords and deliver OTP mail without holding the application lock.

    Only the resulting challenge is committed, after rechecking account and
    invitation ownership. A provider failure leaves no usable challenge behind.
    """
    path=request.scope['route'].path
    with read_transaction(maintain=False) as snapshot:
        original_users={key:(u['password_hash'],u.get('token_version',0),u['approved'])
                        for key,u in snapshot['users'].items()}
        original_id=getattr(body,'challenge_id',None)
        original_challenge=dict(snapshot['challenges'].get(original_id,{}))
        result=dispatch(snapshot,request,body)
    cid=result.get('challenge_id')
    prepared=snapshot['challenges'].get(cid)
    if not prepared:return result  # Unknown reset account: generic response.
    with transaction() as current:
        key=prepared['portal']+':'+prepared['email']
        user=prepared['pending'] or snapshot['users'].get(key)
        if prepared['purpose']=='signup':
            require(key not in current['users'],ACCOUNT_EXISTS_MESSAGE,409)
            require(not any(u['portal']==user['portal'] and u['phone']==user['phone']
                            for u in current['users'].values()),ACCOUNT_EXISTS_MESSAGE,409)
            if prepared['portal']=='admin':
                invitation=current['invites'].get(hashlib.sha256(prepared['invitation'].encode()).hexdigest())
                require(invitation and not invitation.get('used') and invitation['expires']>time.time()
                        and invitation['email']==prepared['email'] and invitation['role']==user['role'],
                        'Invitation is no longer valid.',403)
        else:
            live_user=current['users'].get(key)
            require(live_user and (live_user['password_hash'],live_user.get('token_version',0),live_user['approved'])==original_users.get(key),
                    'Account changed. Start verification again.',409)
            if prepared['purpose']=='signin':require(live_user['approved'],'Your driver application is awaiting administrator approval.',403)
            # Preserve the existing legacy password-hash upgrade.
            live_user['password_hash']=user['password_hash']
        if original_id:
            require(current['challenges'].get(original_id)==original_challenge,
                    'Verification changed. Start again with the latest code.',409)
        for ident in [k for k,v in current['challenges'].items() if v['expires']<time.time()
                      or (v['email']==prepared['email'] and v['portal']==prepared['portal'])]:
            del current['challenges'][ident]
        current['challenges'][cid]=prepared
    return result


def rate_limit(bucket, limit, seconds):
    if DATABASE_URL:
        return postgres_rate_limit(bucket,limit,seconds)
    # Independent transaction: rejected attempts cannot roll back their counters.
    c = sqlite3.connect(DB_PATH, timeout=30, isolation_level=None)
    try:
        c.execute('CREATE TABLE IF NOT EXISTS rate_limits (bucket TEXT PRIMARY KEY, until REAL NOT NULL, hits INTEGER NOT NULL)')
        c.execute('BEGIN IMMEDIATE')
        t = time.time()
        hits=c.execute('''INSERT INTO rate_limits (bucket,until,hits) VALUES (?,?,1)
            ON CONFLICT (bucket) DO UPDATE SET
            hits=CASE WHEN rate_limits.until<=? THEN 1 ELSE min(rate_limits.hits+1,?) END,
            until=CASE WHEN rate_limits.until<=? THEN excluded.until ELSE rate_limits.until END
            RETURNING hits''',(bucket,t+seconds,t,limit+1,t)).fetchone()[0]
        if secrets.randbelow(256)==0:
            c.execute('DELETE FROM rate_limits WHERE bucket IN (SELECT bucket FROM rate_limits WHERE until<? ORDER BY until LIMIT 128)',(t,))
        c.commit()
        require(hits <= limit, 'Too many attempts. Please try again later.', 429)
    finally: c.close()


def audit(s, actor, event, object_id=''):
    s['revision'] += 1
    s['audit'].append(dict(id=uid('AUD'),actor=actor,event=event,object_id=object_id,timestamp=now()))
    s['audit'] = s['audit'][-5000:]


def send_code(email, code, purpose='signin', portal='customer', name='', promotion=None):
    api_key = os.getenv('RESEND_API_KEY', '')
    require(bool(api_key), 'Email delivery is not configured. The administrator must set RESEND_API_KEY.', 503)
    kind = {'signup':'signup_verification','signin':'signin_verification','reset':'password_reset'}.get(purpose,'verification')
    content = build_email(kind, code=code, name=name, details=[('Portal', portal.title())], company_ad=promotion)
    try:
        r = httpx.post(
            'https://api.resend.com/emails',
            headers={'Authorization': 'Bearer ' + api_key},
            json={
                'from': sender_for(kind),
                'to': [email],
                'subject': content['subject'],
                'html': content['html'],
                'text': content['text'],
            },
            timeout=15,
        )
        require(r.is_success, 'Unable to deliver verification email. Please try later.', 503)
    except httpx.HTTPError:
        raise HTTPException(503, 'Unable to deliver verification email. Please try later.')

class Signup(BaseModel):
    email: EmailStr
    phone: str
    password: str = Field(min_length=10,max_length=128)
    full_name: str = Field(min_length=1,max_length=150)
    address: str = Field(min_length=1,max_length=1000)
    portal: Literal['customer','admin','driver'] = 'customer'
    invitation: str = Field(default='',max_length=200)
    role: str = 'customer'

class Signin(BaseModel):
    identifier: str = Field(min_length=1,max_length=254)
    password: str = Field(min_length=1,max_length=128)
    portal: Literal['customer','admin','driver'] = 'customer'

class OTPRequest(BaseModel):
    challenge_id: str = Field(min_length=1,max_length=100)

class OTPVerify(BaseModel):
    email: Optional[EmailStr] = None
    otp: str = Field(pattern=r'^\d{6}$')
    challenge_id: str = Field(min_length=1,max_length=100)

class CartLine(BaseModel):
    product_id: str = Field(min_length=1,max_length=100)
    name: str = ''
    quantity: int = Field(strict=True,ge=1,le=10000)
    price: float = 0  # Compatibility only; server price is authoritative.

class Checkout(BaseModel):
    items: list[CartLine] = Field(min_length=1,max_length=100)
    customer_email: str = ''
    customer_phone: str = ''
    customer_name: str = ''
    delivery_address: str = Field(default='',max_length=1000)
    address_id: str = Field(default='',max_length=100)
    total_amount: float = 0  # Compatibility only; ignored.
    currency: Literal['INR'] = 'INR'
    payment_method: Literal['COD','Online','UPI / Online (INR)'] = 'COD'
    delivery_date: str = ''

class StockUpdate(BaseModel):
    stock_quantity: int = Field(strict=True,ge=0,le=10000000)
    price: Optional[float] = Field(default=None,ge=0,le=1000000,allow_inf_nan=False)
    version: Optional[int] = None
    tax_rate: Optional[float] = Field(default=None,ge=0,le=100)

class PaymentVerify(BaseModel):
    order_id: str
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str

class EmptyBody(BaseModel):
    model_config = ConfigDict(extra='allow')


def issue_challenge(s, u, purpose, pending=None, invitation=''):
    email = u['email']
    for k in [k for k,v in s['challenges'].items() if v['expires'] < time.time() or (v['email']==email and v['portal']==u['portal'])]:
        del s['challenges'][k]
    cid, code = uid('CH'), f'{secrets.randbelow(1000000):06d}'
    from experience import company_ad
    send_code(email,code,purpose,u['portal'],u.get('full_name',''),company_ad(s))
    sent_at = time.time()
    s['challenges'][cid] = dict(email=email,portal=u['portal'],purpose=purpose,pending=pending,
        invitation=invitation, code_hash=hmac.new(SECRET.encode(),(cid+code).encode(),'sha256').hexdigest(),
        expires=sent_at+300,attempts=0,sent_at=sent_at)
    return {'challenge_id':cid,'otp_required':True,'message':'Verification code sent to your registered email.','expires_in':300,
            'server_time':sent_at,'resend_available_at':sent_at+60,'expires_at':sent_at+300}


def session(s,u):
    jti=uid('SES'); exp=int(time.time())+SESSION_SECONDS
    s['sessions'][jti]={'email':u['email'],'portal':u['portal'],'expires':exp,'version':u.get('token_version',0)}
    token=jwt.encode({'sub':u['email'],'portal':u['portal'],'role':u['role'],'jti':jti,'exp':exp,
        'iat':int(time.time()),'iss':'first-milk-venture','aud':'fmv-portals'},SECRET,algorithm='HS256')
    return dict(access_token=token,token_type='bearer',expires_in=SESSION_SECONDS,user=public_user(u))


def actor(s, request, portal=None, permission=None):
    token=request.headers.get('authorization','')
    if not token.startswith('Bearer '):
        # Explicitly reject all legacy shared-key paths, including forged cached tokens.
        raise HTTPException(403 if request.headers.get('x-admin-key') or request.headers.get('x-driver-key') else 401,'Sign in with password and email OTP.')
    try:
        p=jwt.decode(token[7:],SECRET,algorithms=['HS256'],issuer='first-milk-venture',audience='fmv-portals',
            options={'require':['exp','iat','sub','jti','portal']})
        sess=s['sessions'].get(p['jti']); u=s['users'].get(p['portal']+':'+p['sub'])
        require(sess and u and u['approved'] and sess['expires']>time.time() and sess['version']==u.get('token_version',0), 'Session expired. Sign in again.',401)
        require(u['portal']==sess['portal'] and u['email']==sess['email'],'Invalid session.',401)
        if portal: require(u['portal']==portal,'This portal requires a different account role.',403)
        if permission:
            perms=PERMISSIONS.get(u['role'],set())
            require('*' in perms or permission in perms,'Your role does not allow this action.',403)
        return u,p['jti']
    except jwt.InvalidTokenError: raise HTTPException(401,'Session expired or invalid. Sign in again.')


def mine(s,collection,u): return [v for v in s[collection].values() if v.get('owner')==u['email']]
def owned(s,collection,ident,u):
    v=s[collection].get(ident)
    require(v and v.get('owner')==u['email'],'Record not found.',404)
    return v

def notify(s,email,title,message,portal='customer'):
    n=dict(id=uid('NTF'),owner=email,portal=portal,title=title,message=message,read=False,timestamp=now())
    s['notifications'][n['id']]=n


def order_view(o, driver=False):
    hidden={'delivery_otp','otp_hash','otp_attempts'} if driver else {'otp_hash','otp_attempts'}
    return {k:v for k,v in o.items() if k not in hidden and k not in {'epod_photo_url','epod_signature_url'}}


def make_order(s,u,body,key):
    require(re.fullmatch('[A-Za-z0-9_-]{16,100}',key or '') is not None,'Idempotency-Key header of 16–100 letters, digits, _ or - is required.',422)
    payload=body.model_dump()
    digest=hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
    ik=u['id']+':'+key
    if ik in s['idempotency']:
        item=s['idempotency'][ik];require(item['digest']==digest,'This checkout key was already used with different items.',409)
        return s['orders'][item['order_id']]
    from experience import resolve_checkout_address
    addr=resolve_checkout_address(s,u,body)
    require(bool(addr),'A delivery address is required.')
    delivery_day=body.delivery_date or today()
    try: require(date.fromisoformat(delivery_day)>=date.fromisoformat(today()),'Choose today or a future delivery date.')
    except ValueError: raise HTTPException(422,'Invalid delivery date.')
    # Aggregate duplicate lines before stock validation; all products validated before any mutation.
    quantities={}
    for line in body.items: quantities[line.product_id]=quantities.get(line.product_id,0)+line.quantity
    lines=[];subtotal=tax=0
    for pid,qty in quantities.items():
        p=s['products'].get(pid)
        require(p and not p.get('deleted'), 'A selected product is unavailable.',404)
        require(qty<=p['stock_quantity'],f"Insufficient stock for {p['name']}.",409)
        amount=money(p['price'])*qty
        line_tax=int((Decimal(amount)*Decimal(str(p.get('tax_rate',0)))/100).quantize(Decimal('1'),rounding=ROUND_HALF_UP))
        lines.append(dict(product_id=pid,name=p['name'],price=p['price'],quantity=qty,amount_paise=amount,tax_paise=line_tax))
        subtotal+=amount;tax+=line_tax
    online=body.payment_method!='COD'
    if online:
        require(os.getenv('RAZORPAY_KEY_ID') and os.getenv('RAZORPAY_KEY_SECRET'),'Online payments are not configured. Choose cash on delivery.',503)
    chosen=s.get('addresses',{}).get(body.address_id,{})
    oid=uid('FMV'); otp=f'{secrets.randbelow(9000)+1000}'
    o=dict(id=oid,order_number=oid,owner=u['email'],user_email=u['email'],customer_name=chosen.get('recipient') or u['full_name'],
        customer_phone=chosen.get('phone') or u['phone'],address_id=body.address_id,delivery_address=addr,items=lines,items_summary=', '.join(f"{l['quantity']}x {l['name']}" for l in lines),
        delivery_latitude=chosen.get('latitude'),delivery_longitude=chosen.get('longitude'),
        quantity=sum(quantities.values()),subtotal_paise=subtotal,tax_paise=tax,total_paise=subtotal+tax,total_amount=(subtotal+tax)/100,
        currency='INR',payment_method='Online' if online else 'COD',payment_status='pending' if online else 'cod_due',
        cod_amount=0 if online else (subtotal+tax)/100,cod_collected=0,status='Awaiting Payment' if online else 'Unassigned',
        delivery_date=delivery_day,zone_id='Zone-1',assigned_driver_id=None,delivery_otp=otp,
        otp_hash=hmac.new(SECRET.encode(),(oid+otp).encode(),'sha256').hexdigest(),otp_attempts=0,created_at=now(),
        reservation_expires=time.time()+1800 if online else None)
    for pid,qty in quantities.items(): s['products'][pid]['stock_quantity']-=qty;s['products'][pid]['version']+=1
    s['orders'][oid]=o;s['idempotency'][ik]={'digest':digest,'order_id':oid}
    invoice=dict(id=uid('INV'),owner=u['email'],order_id=oid,date=today(),due_date=delivery_day,total=o['total_amount'],
        status='unpaid',type='Sales Invoice',currency='INR',tax_paise=tax,gstin=s['tax'].get('gstin',''))
    s['invoices'][invoice['id']]=invoice
    notify(s,u['email'],'Order placed',f'Order {oid}. Delivery PIN: {otp}. Share the PIN only at delivery.')
    from experience import queue_order_email
    from workforce import notify_managers
    notify_managers(s,'New order awaiting assignment',oid)
    queue_order_email(s,o,'placed')
    audit(s,u['email'],'ORDER_PLACED',oid)
    return o


def cancel_order(s,o):
    if o['status']=='Cancelled': return
    for l in o['items']:
        s['products'][l['product_id']]['stock_quantity']+=l['quantity'];s['products'][l['product_id']]['version']+=1
    o['status']='Cancelled'
    o['payment_status']='cancelled'
    o['cancelled_at']=now()
    from experience import queue_order_email
    queue_order_email(s,o,'cancelled')
    notify(s,o['owner'],'Order cancelled',f"Your order {o['order_number']} has been cancelled.")
    for inv in s['invoices'].values():
        if inv['order_id']==o['id']: inv['status']='cancelled'


def expire_reservations(s):
    for o in s['orders'].values():
        if o['status']=='Awaiting Payment' and o['reservation_expires']<time.time():
            cancel_order(s,o);audit(s,'system','PAYMENT_RESERVATION_EXPIRED',o['id'])


def razorpay_call(method,path,data=None):
    key,secret=os.getenv('RAZORPAY_KEY_ID'),os.getenv('RAZORPAY_KEY_SECRET')
    require(key and secret,'Online payment service is not configured.',503)
    try:
        r=httpx.request(method,'https://api.razorpay.com/v1/'+path,auth=(key,secret),json=data,timeout=20)
        require(r.is_success,'Payment provider request failed. Please retry.',502)
        return r.json()
    except httpx.HTTPError: raise HTTPException(502,'Payment provider is unavailable.')


def payment_captured(s,o,pid):
    payment=razorpay_call('GET','payments/'+pid)
    require(payment.get('order_id')==o.get('provider_order_id') and payment.get('amount')==o['total_paise'] and payment.get('currency')=='INR', 'Payment does not match this order.',400)
    require(payment.get('status')=='captured','Payment is not captured yet. Please check again shortly.',409)
    require(o['status']!='Cancelled','This reservation expired. Contact support for payment resolution.',409)
    if o['payment_status']=='paid': return
    o.update(payment_status='paid',provider_payment_id=pid,status='Unassigned')
    for inv in s['invoices'].values():
        if inv['order_id']==o['id']:inv['status']='paid'
    audit(s,o['owner'],'PAYMENT_VERIFIED',o['id'])


def shift_times(sh):
    current=time.time();start=sh['started_epoch'];end=sh.get('ended_epoch') or current
    breaks=sh.get('break_seconds',0)+(end-sh['break_started'] if sh.get('break_started') else 0)
    return dict(sh,work_seconds=max(0,int(end-start-breaks)),break_duration_seconds=int(breaks))


def sanitize_photo(data):
    require(isinstance(data,str) and len(data)<2800000 and data.startswith(('data:image/jpeg;base64,','data:image/png;base64,','data:image/webp;base64,')), 'Provide a JPEG, PNG or WebP photograph smaller than 2 MB.',422)
    try:
        raw=base64.b64decode(data.split(',',1)[1],validate=True)
        Image.MAX_IMAGE_PIXELS=16000000
        img=Image.open(io.BytesIO(raw));img.load()
        require(img.width>=16 and img.height>=16 and img.width*img.height<=16000000,'Invalid photograph.',422)
        img.thumbnail((1600,1600));output=io.BytesIO();img.convert('RGB').save(output,'JPEG',quality=82)
        return 'data:image/jpeg;base64,'+base64.b64encode(output.getvalue()).decode()
    except HTTPException: raise
    except Exception: raise HTTPException(422,'The photograph could not be decoded.')


def dispatch(s, request, body=None):
    path=request.scope['route'].path;method=request.method;params=request.path_params
    data=body.model_dump(exclude_unset=True) if isinstance(body,BaseModel) else (body or {})
    if path=='/': return {'status':'online','service':'First Milk Venture Enterprise API','version':request.app.version,'country':'India','state':'Kerala','currency':'INR'}
    if path=='/config': return {'country':'India','state':'Kerala','currency':'INR','timezone':'Asia/Kolkata','online_payments':bool(os.getenv('RAZORPAY_KEY_ID') and os.getenv('RAZORPAY_KEY_SECRET'))}
    if path=='/auth/signup':
        valid_password(body.password);email=str(body.email).lower();key=body.portal+':'+email
        require(key not in s['users'],ACCOUNT_EXISTS_MESSAGE,409)
        phone=phone_india(body.phone)
        require(clean_text(body.full_name,150) and clean_text(body.address,1000),'Name and address cannot be blank.',422)
        require(not any(u['portal']==body.portal and u['phone']==phone for u in s['users'].values()),ACCOUNT_EXISTS_MESSAGE,409)
        role={'customer':'customer','driver':'driver','admin':'operations_manager'}[body.portal]
        if body.portal=='admin':
            inv=s['invites'].get(hashlib.sha256(body.invitation.encode()).hexdigest())
            require(inv and inv['email']==email and inv['expires']>time.time() and not inv.get('used'),'A valid administrator invitation is required.',403)
            role=inv['role']
        u=dict(id=uid('USR'),email=email,phone=phone,password_hash=password_hash(body.password),full_name=clean_text(body.full_name,150),
            address=clean_text(body.address,1000),portal=body.portal,role=role,approved=body.portal!='driver',token_version=0,created_at=now())
        return issue_challenge(s,u,'signup',pending=u,invitation=body.invitation)
    if path=='/auth/signin':
        target=body.identifier.strip().lower()
        u=next((u for u in s['users'].values() if u['portal']==body.portal and (u['email']==target or u['phone']==re.sub(r'[\s()-]','',target) or u['phone']=='+91'+target)),None)
        match=password_ok(body.password,u['password_hash'] if u else DUMMY_HASH)
        require(u and match,'Invalid sign-in details.',401)
        if u['password_hash'].startswith('scrypt$'):u['password_hash']=password_hash(body.password)
        require(u['approved'],'Your driver application is awaiting administrator approval.',403)
        return issue_challenge(s,u,'signin')
    if path=='/auth/request-otp':
        ch=s['challenges'].get(body.challenge_id)
        require(ch and ch['expires']>time.time() and not ch.get('reset_verified'),'Verification expired. Start sign-in again.',400)
        server_time = time.time()
        resend_available_at = ch['sent_at'] + 60
        if server_time < resend_available_at:
            raise HTTPException(429, 'Wait 60 seconds before requesting another code.', headers={
                'Retry-After': str(math.ceil(resend_available_at-server_time)),
                'X-Server-Time': str(server_time),
                'X-Resend-Available-At': str(resend_available_at),
                'X-OTP-Expires-At': str(ch['expires']),
            })
        u=ch['pending'] or s['users'].get(ch['portal']+':'+ch['email'])
        return issue_challenge(s,u,ch['purpose'],ch['pending'],ch.get('invitation',''))
    if path=='/auth/verify-otp':
        ch=s['challenges'].get(body.challenge_id)
        if not ch or ch['expires']<=time.time() or ch['attempts']>=5 or ch.get('reset_verified'):
            return JSONResponse({'detail':'Verification expired or locked. Start sign-in again.'},status_code=400)
        digest=hmac.new(SECRET.encode(),(body.challenge_id+body.otp).encode(),'sha256').hexdigest()
        ch['attempts']+=1
        if not hmac.compare_digest(digest,ch['code_hash']):
            return JSONResponse({'detail':'Invalid verification code.'},status_code=400)
        if ch['purpose']=='reset':
            proof = secrets.token_urlsafe(32)
            ch['reset_proof_hash'] = hashlib.sha256(proof.encode()).hexdigest()
            ch['reset_verified']=True
            del ch['code_hash']
            return {'reset_token':body.challenge_id + '.' + proof,'message':'Code verified. Set your new password.'}
        u=ch['pending'] or s['users'].get(ch['portal']+':'+ch['email'])
        require(u is not None,'Account not found.',404)
        if ch['purpose']=='signup':
            require(ch['portal']+':'+ch['email'] not in s['users'],ACCOUNT_EXISTS_MESSAGE,409)
            # Another pending signup may have verified this mobile while this
            # code was in transit. Recheck ownership in the same transaction.
            require(not any(existing['portal']==u['portal'] and existing['phone']==u['phone']
                            for existing in s['users'].values()),ACCOUNT_EXISTS_MESSAGE,409)
            if ch['portal']=='admin':
                inv=s['invites'].get(hashlib.sha256(ch['invitation'].encode()).hexdigest())
                require(inv and not inv.get('used') and inv['expires']>time.time(),'Invitation is no longer valid.',403)
                inv['used']=True
            s['users'][ch['portal']+':'+ch['email']]=u
            if ch['portal']=='driver':s['drivers'][u['id']]=dict(id=u['id'],email=u['email'],name=u['full_name'],phone=u['phone'],address=u['address'],approved=False)
        del s['challenges'][body.challenge_id]
        audit(s,u['email'],'OTP_VERIFIED')
        if not u['approved']:return {'pending_approval':True,'message':'Application submitted successfully. You will receive an email notification once your driver profile has been approved.'}
        return dict(session(s,u),message='Verification successful.')
    if path in {'/portal/auth/password-reset/request','/auth/password-reset/request'}:
        email=str(data.get('email','')).strip().lower();portal=data.get('portal','customer')
        u=s['users'].get(portal+':'+email)
        if u:return issue_challenge(s,u,'reset')
        return {'message':'If this account exists, a verification code has been sent.','challenge_id':uid('CH'),'otp_required':True}
    if path in {'/portal/auth/password-reset/confirm','/auth/password-reset/confirm'}:
        token = data.get('reset_token')
        invalid_reset = 'Invalid or expired password reset.'
        require(isinstance(token, str) and 0 < len(token) <= 160 and token.isascii(), invalid_reset)
        challenge_id, separator, proof = token.partition('.')
        ch = s['challenges'].get(challenge_id)
        require(separator and proof and ch and ch['purpose']=='reset' and ch.get('reset_verified')
                and ch['expires']>time.time() and hmac.compare_digest(
                    ch.get('reset_proof_hash', ''), hashlib.sha256(proof.encode()).hexdigest()), invalid_reset)
        valid_password(data.get('new_password',''));u=s['users'][ch['portal']+':'+ch['email']]
        u['password_hash']=password_hash(data['new_password']);u['token_version']+=1
        del s['challenges'][challenge_id]
        audit(s,u['email'],'PASSWORD_RESET');return {'message':'Password changed. Sign in again.'}
    if path in {'/admin/rbac/verify','/driver/verify-key','/portal/auth/sso/initiate'}:
        raise HTTPException(503 if 'sso' in path else 410,'Use email/password and OTP. External SSO requires a configured identity provider.' if 'sso' in path else 'Shared key login is disabled. Use email/password and OTP.')
    if path=='/products':
        actor(s,request)
        return [p for p in s['products'].values() if not p.get('deleted')]
    if path=='/auth/session':
        u,_=actor(s,request);return {'user':public_user(u),'revision':s['revision']}
    if path=='/auth/logout':
        _,sid=actor(s,request);del s['sessions'][sid];return {'message':'Signed out.'}
    if path=='/sync':
        u,_=actor(s,request)
        return {'revision':s['revision'],'server_time':now(),'portal':u['portal']}
    if path=='/webhooks/payment':
        raise HTTPException(400,'Use the signed raw-body webhook endpoint.')
    portal='admin' if path.startswith('/admin/') else 'driver' if path.startswith('/driver/') else 'customer'
    permission=None
    if portal=='admin':
        module=path.split('/')[2]
        permission={'products':'catalog','drivers':'drivers','deliveries':'orders','generate-deliveries':'orders','finance':'finance','support':'support','attendance':'drivers','sos':'drivers','documents':'support'}.get(module,'security')
    # Admin dispatch screen has its own route and does not reuse a driver's identity.
    u,_=actor(s,request,portal,permission)
    if path=='/users/me':
        if method=='PATCH':
            for field in ('full_name','address'):
                if data.get(field) is not None:u[field]=clean_text(data[field],1000)
            if data.get('phone') is not None:
                ph=phone_india(data['phone'])
                require(not any(x['id']!=u['id'] and x['portal']=='customer' and x['phone']==ph for x in s['users'].values()),'Phone already registered.',409)
                u['phone']=ph
            audit(s,u['email'],'PROFILE_UPDATED')
        return {'user':public_user(u),'message':'Profile saved.'}
    if path=='/orders/checkout':return {'status':'success','order':order_view(make_order(s,u,body,request.headers.get('idempotency-key')))}
    if path in {'/orders','/deliveries'}:return [order_view(o) for o in mine(s,'orders',u)]
    if path=='/orders/{order_id}/cancel':
        o=owned(s,'orders',params['order_id'],u)
        require(o['status'] in {'Unassigned','Awaiting Payment','Cancelled'} and o['payment_status']!='paid','This order requires support assistance to cancel.',409)
        cancel_order(s,o);audit(s,u['email'],'ORDER_CANCELLED',o['id']);return {'order':order_view(o)}
    if path=='/payments/create':
        o=owned(s,'orders',data.get('order_id'),u)
        require(o['payment_method']=='Online' and o['status']=='Awaiting Payment','Order is not awaiting online payment.',409)
        if not o.get('provider_order_id'):
            provider=razorpay_call('POST','orders',{'amount':o['total_paise'],'currency':'INR','receipt':o['id']})
            o['provider_order_id']=provider['id']
        return {'key_id':os.getenv('RAZORPAY_KEY_ID'),'provider_order_id':o['provider_order_id'],'amount':o['total_paise'],'currency':'INR','order_id':o['id']}
    if path=='/payments/verify':
        o=owned(s,'orders',body.order_id,u)
        require(o.get('provider_order_id')==body.razorpay_order_id,'Payment order mismatch.')
        sig=hmac.new(os.getenv('RAZORPAY_KEY_SECRET','').encode(),(o['provider_order_id']+'|'+body.razorpay_payment_id).encode(),'sha256').hexdigest()
        require(hmac.compare_digest(sig,body.razorpay_signature),'Invalid payment signature.',400)
        payment_captured(s,o,body.razorpay_payment_id);return {'status':'success','order':order_view(o)}
    if path=='/subscriptions':
        if method=='GET':return mine(s,'subscriptions',u)
        p=s['products'].get(data['product_id']);require(p and not p.get('deleted'),'Product not found.',404)
        require(data['quantity']>0 and data['frequency'] in {'Daily','Weekly','Alternate Days'},'Invalid subscription.')
        try:require(date.fromisoformat(data['start_date'])>=date.fromisoformat(today()),'Start date is in the past.')
        except ValueError:raise HTTPException(422,'Invalid start date.')
        sub=dict(data,id=uid('SUB'),owner=u['email'],status='active',product_name=p['name'],version=1,created_at=now())
        s['subscriptions'][sub['id']]=sub
        from workforce import subscription_email
        subscription_email(s,u,sub,'created')
        audit(s,u['email'],'SUBSCRIPTION_CREATED',sub['id']);return {'subscription':sub,'message':'Subscription scheduled. Stock is deducted for each generated delivery.'}
    if path=='/subscriptions/{subscription_id}':
        sub=owned(s,'subscriptions',params['subscription_id'],u)
        status=data.get('status')
        require(status in {'active','paused','cancelled'},'Invalid subscription status.')
        if status==sub['status']:return sub
        require(sub['status']!='cancelled','A cancelled subscription cannot be resumed. Create a new subscription.',409)
        sub.update(status=status,version=sub.get('version',1)+1,updated_at=now())
        from workforce import subscription_email
        subscription_email(s,u,sub,{'paused':'paused','active':'resumed','cancelled':'cancelled'}[status])
        audit(s,u['email'],'SUBSCRIPTION_UPDATED',sub['id']);return sub
    if portal=='admin':return admin_dispatch(s,u,request,data)
    if portal=='driver':return driver_dispatch(s,u,request,data)
    return customer_portal_dispatch(s,u,request,data)


def admin_dispatch(s,u,r,d):
    p=r.scope['route'].path;m=r.method;pp=r.path_params
    if p=='/admin/products':
        money(d['price']);require(d['stock_quantity']>=0 and d['min_stock_threshold']>=0,'Invalid stock quantity.')
        require(d['name'].strip() and len(d['name'])<=150,'Product name is required.')
        image_url=d.get('image_url','')
        if image_url.startswith('/media/products/'):
            require(image_url.rsplit('/',1)[-1] in s.get('product_media_index',{}),'Product image not found.',404)
        else:require(not image_url or image_url.startswith('https://'),'Image must use HTTPS.')
        product=dict(d,id=uid('PROD'),currency='INR',version=1,tax_rate=0)
        s['products'][product['id']]=product;audit(s,u['email'],'PRODUCT_CREATED',product['id']);return {'product':product,'message':'Product created.'}
    if p in {'/admin/products/{product_id}/stock','/admin/products/{product_id}'}:
        prod=s['products'].get(pp['product_id']);require(prod and not prod.get('deleted'),'Product not found.',404)
        if m=='DELETE':prod['deleted']=True
        else:
            if d.get('version') is not None:require(prod['version']==d['version'],'Product changed. Refresh before saving.',409)
            prod['stock_quantity']=d['stock_quantity']
            if d.get('price') is not None:money(d['price']);prod['price']=d['price']
            if d.get('tax_rate') is not None:prod['tax_rate']=d['tax_rate']
        prod['version']+=1;audit(s,u['email'],'PRODUCT_UPDATED',prod['id']);return {'product':prod,'message':'Inventory saved.'}
    if p=='/admin/drivers':
        if m=='GET':return [dict({k:item for k,item in v.items() if k!='invitation_hash'},key=v['id']) for v in s['drivers'].values() if not v.get('deleted')]
        # Legacy create-driver schema retained: supplied key becomes an invitation hash, never an account password.
        require(len(d['driver_key'])>=16,'Driver invitation code must have at least 16 characters.')
        ph=phone_india(d['phone']);ident=uid('DRV')
        record=dict(id=ident,name=clean_text(d['name'],150),phone=ph,address=clean_text(d['address'],1000),approved=False,
            invitation_hash=hashlib.sha256(d['driver_key'].encode()).hexdigest(),email='',invited=True)
        s['drivers'][ident]=record;audit(s,u['email'],'DRIVER_INVITED',ident)
        return {'message':'Driver invitation recorded. Driver must register, verify email, and receive approval.','driver':{k:v for k,v in record.items() if k!='invitation_hash'}}
    if p=='/admin/drivers/{driver_id}/approve':
        dr=s['drivers'].get(pp['driver_id']);require(dr and dr.get('email') and not dr.get('deleted'),'Registered driver not found.',404)
        if not dr['approved']:
            dr['approved']=True;s['users']['driver:'+dr['email']]['approved']=True
            from experience import queue_email
            queue_email(s,'driver-approved:'+dr['id'],dr['email'],'driver_approval',name=dr['name'],
                message='Your driver profile has been approved successfully. Sign in with your password and email verification code.')
            notify(s,dr['email'],'Driver profile approved','Your driver profile has been approved successfully.',portal='driver')
            audit(s,u['email'],'DRIVER_APPROVED',dr['id'])
        return {'message':'Driver approved. Approval email queued; they can sign in with OTP.'}
    if p=='/admin/drivers/{driver_key}':
        ident=pp['driver_key'];dr=s['drivers'].get(ident);require(dr,'Driver not found.',404)
        require(not any(o.get('assigned_driver_id')==ident and o['status'] not in {'Delivered','Cancelled','Failed'} for o in s['orders'].values()),'Reassign active deliveries before removing this driver.',409)
        dr['deleted']=True
        if dr.get('email'):
            usr=s['users']['driver:'+dr['email']];usr['approved']=False;usr['token_version']+=1
        audit(s,u['email'],'DRIVER_REMOVED',ident);return {'message':'Driver disabled.'}
    if p=='/admin/deliveries':
        day=r.query_params.get('delivery_date');zone=r.query_params.get('zone_id')
        return [order_view(o) for o in s['orders'].values() if (not day or o['delivery_date']==day) and (not zone or o['zone_id']==zone)]
    if p=='/admin/deliveries/{delivery_id}/assign':
        o=s['orders'].get(pp['delivery_id']);dr=s['drivers'].get(d['driver_key'])
        require(o and dr and dr['approved'] and not dr.get('deleted'),'Order or approved driver not found.',404)
        require(o['status'] not in {'Delivered','Cancelled','Awaiting Payment'},'Order cannot be assigned.',409)
        previous_driver=o.get('assigned_driver_id')
        o.update(assigned_driver_id=dr['id'],assigned_driver=dr['name'],status='Assigned')
        if previous_driver!=dr['id']:notify(s,dr['email'],'New delivery assigned',o['order_number'],portal='driver')
        audit(s,u['email'],'DRIVER_ASSIGNED',o['id']);return {'message':'Driver assignment saved.','order':order_view(o)}
    if p=='/admin/generate-deliveries':
        target=d.get('target_date') or today()
        try:target_date=date.fromisoformat(target);require(target_date>=date.fromisoformat(today()),'Dispatch date is in the past.')
        except ValueError:raise HTTPException(422,'Invalid dispatch date.')
        created=[]
        for sub in list(s['subscriptions'].values()):
            if sub['status']!='active':continue
            delta=(target_date-date.fromisoformat(sub['start_date'])).days
            if delta<0 or (sub['frequency']=='Weekly' and delta%7) or (sub['frequency']=='Alternate Days' and delta%2):continue
            user=s['users'].get('customer:'+sub['owner']);require(user,'Subscription owner not found.',409)
            key='subscription_'+sub['id']+'_'+target
            body=Checkout(items=[CartLine(product_id=sub['product_id'],quantity=sub['quantity'])],delivery_date=target,payment_method='COD')
            o=make_order(s,user,body,key);o['subscription_id']=sub['id'];created.append(o['id'])
        audit(s,u['email'],'DELIVERIES_GENERATED',target);return {'message':'Dispatch generation completed.','orders':created}
    if p=='/admin/attendance':return {'shifts':[shift_times(sh) for sh in s['shifts'].values()]}
    if p=='/admin/sos':return {'alerts':list(s['sos'].values())}
    if p=='/admin/finance/overview':
        paid=[o for o in s['orders'].values() if o['payment_status'] in {'paid','cod_collected'}]
        return {'ledger':{'gross_revenue':sum(o['total_paise'] for o in paid)/100,'vat_collected':sum(o['tax_paise'] for o in paid)/100,'driver_payouts':0},'tax_compliance':s['tax'],'refund_requests':list(s['rma'].values())}
    if p=='/admin/finance/tax-config':
        require(0<=d['vat_rate']<=100,'Invalid tax rate.')
        gst=d.get('zatca_tin','').upper()
        require(not gst or re.fullmatch(r'\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]',gst),'Enter a valid GSTIN.')
        s['tax'].update(gstin=gst,zatca_tin=gst,vat_rate=d['vat_rate'],zatca_mode='india_gst')
        audit(s,u['email'],'GST_SETTINGS_UPDATED');return {'message':'India GST settings saved. Set the applicable rate per product.'}
    if p=='/admin/finance/refunds/{refund_id}':
        claim=s['rma'].get(pp['refund_id']);require(claim,'Claim not found.',404)
        require(d['status'] in {'approved','rejected'},'Invalid refund decision.')
        require(claim['status']=='under_review','Claim already reviewed.',409)
        claim['status']=d['status'];claim['reviewed_at']=now()
        # A review is not a payment-provider refund; preserve that distinction in UI.
        audit(s,u['email'],'REFUND_REVIEW_'+d['status'].upper(),claim['id']);return {'message':'Refund decision saved. Any money refund must be completed through the payment provider.','claim':claim}
    if p=='/admin/support/users':return {'users':[public_user(x) for x in s['users'].values() if x['portal']=='customer']}
    if p=='/admin/support/tickets':
        if m=='GET':return {'tickets':list(s['tickets'].values())}
        email=d['customer_email'].lower();require('customer:'+email in s['users'],'Customer not found.',404)
        ticket=dict(d,id=uid('TKT'),owner=email,subject=d['summary'],description=d['summary'],status='open',history=[],created_at=now())
        s['tickets'][ticket['id']]=ticket;audit(s,u['email'],'TICKET_CREATED',ticket['id']);return {'ticket':ticket,'message':'Ticket created.'}
    if p=='/admin/support/tickets/{ticket_id}':
        t=s['tickets'].get(pp['ticket_id']);require(t,'Ticket not found.',404)
        require(d['status'] in {'open','in_progress','resolved'},'Invalid ticket status.')
        t['status']=d['status'];t['history'].append({'sender':'support','message':'Status updated to '+d['status'],'timestamp':now()})
        notify(s,t['owner'],'Support ticket updated',t['subject']);audit(s,u['email'],'TICKET_UPDATED',t['id']);return {'message':'Ticket updated.','ticket':t}
    if p=='/admin/rbac/roles':return {'roles':{key:{'label':key,'permissions':list(v),'access_key':'Password and OTP required'} for key,v in PERMISSIONS.items()}}
    if p=='/admin/invitations':
        require(u['role']=='super_admin','Only a super admin can invite staff.',403)
        email=str(TypeAdapter(EmailStr).validate_python(d.get('email',''))).lower();role=d.get('role')
        require(role in ROLES,'Invalid role.')
        token=secrets.token_urlsafe(32);s['invites'][hashlib.sha256(token.encode()).hexdigest()]={'email':email,'role':role,'expires':time.time()+86400,'used':False}
        from experience import queue_invitation_email
        queue_invitation_email(s,email,token,role)
        audit(s,u['email'],'STAFF_INVITED',email);return {'invitation':token,'message':'Invitation created. Share this code privately with the named staff member.'}
    if p in {'/admin/key','/admin/rbac/roles/{target_role}/key'}:raise HTTPException(410,'Shared role keys were replaced by individual accounts. Use password reset and staff invitations.')
    raise HTTPException(404,'Unknown administration action.')


def driver_dispatch(s,u,r,d):
    p=r.scope['route'].path;m=r.method;pp=r.path_params
    dr=s['drivers'].get(u['id']);require(dr and dr['approved'] and not dr.get('deleted'),'Driver account is not active.',403)
    if p=='/driver/profile':
        if m=='POST':
            ph=phone_india(d['phone']);name=clean_text(d['name'],150);addr=clean_text(d['address'],1000)
            require(name and addr,'Name and address are required.')
            dr.update(name=name,phone=ph,address=addr);u.update(full_name=name,phone=ph,address=addr)
            audit(s,u['email'],'DRIVER_PROFILE_UPDATED')
        return {'driver':{k:v for k,v in dr.items() if k!='invitation_hash'},'message':'Driver profile saved.'}
    active=next((sh for sh in s['shifts'].values() if sh['driver_id']==u['id'] and sh['status']!='completed'),None)
    if p=='/driver/shifts/active':return {'shift':shift_times(active) if active else None}
    if p=='/driver/shifts':return {'shifts':[shift_times(sh) for sh in s['shifts'].values() if sh['driver_id']==u['id']]}
    if p=='/driver/shifts/clock-in':
        require(not active,'A shift is already active.',409)
        require(d['odometer_start']>=0 and d['vehicle_plate'].strip(),'Vehicle and non-negative odometer are required.')
        checklist=d.get('checklist_json',{})
        require(all(checklist.get(k) is True for k in ('tires','brakes','fuel','cold_chain')),'Complete all four safety checks.')
        sh=dict(d,id=uid('SHIFT'),driver_id=u['id'],driver_name=u['full_name'],status='active',clock_in=now(),started_epoch=time.time(),break_seconds=0,break_started=None,events=[{'status':'active','at':now()}])
        s['shifts'][sh['id']]=sh;audit(s,u['email'],'SHIFT_CLOCK_IN',sh['id']);return {'shift':shift_times(sh)}
    if p=='/driver/shifts/{shift_id}':
        sh=s['shifts'].get(pp['shift_id']);require(sh and sh['driver_id']==u['id'],'Shift not found.',404)
        require(sh['status']!='completed','Shift is already completed.',409)
        status=d.get('status');require(status in {'active','on_break','completed'},'Invalid shift status.')
        require(status!=sh['status'],'Shift is already in this state.',409)
        if status=='completed':require(d.get('odometer_end') is not None and d['odometer_end']>=sh['odometer_start'],'Closing odometer must be at least the starting value.')
        if sh.get('break_started'):sh['break_seconds']+=time.time()-sh['break_started'];sh['break_started']=None
        if status=='on_break':sh['break_started']=time.time()
        sh['status']=status;sh['events'].append({'status':status,'at':now()})
        if status=='completed':sh.update(clock_out=now(),ended_epoch=time.time(),odometer_end=d['odometer_end'])
        audit(s,u['email'],'SHIFT_'+status.upper(),sh['id']);return {'shift':shift_times(sh)}
    if p=='/driver/deliveries':
        day=r.query_params.get('delivery_date')
        return [order_view(o,True) for o in s['orders'].values() if o.get('assigned_driver_id')==u['id'] and (not day or o['delivery_date']==day)]
    if p in {'/driver/deliveries/{delivery_id}','/driver/deliveries/{delivery_id}/epod'}:
        o=s['orders'].get(pp['delivery_id']);require(o and o.get('assigned_driver_id')==u['id'],'Delivery not found.',404)
        status=d.get('status');require(status in {'Picked Up','In Transit','Arrived','Delivered','Failed'},'Invalid delivery status.')
        require(active and active['status']=='active','Clock in and end your break before updating deliveries.',409)
        require(o['status'] not in {'Delivered','Cancelled','Failed'},'Delivery is already closed.',409)
        if status=='Delivered':
            require(p.endswith('/epod'),'Photo and customer PIN are required. Use ePOD.',400)
            if o['otp_attempts']>=5:return JSONResponse({'detail':'Delivery PIN is locked. Contact dispatch.'},status_code=429)
            otp=str(d.get('delivery_otp',''))
            digest=hmac.new(SECRET.encode(),(o['id']+otp).encode(),'sha256').hexdigest()
            if not hmac.compare_digest(digest,o['otp_hash']):
                o['otp_attempts']+=1
                return JSONResponse({'detail':'Incorrect customer delivery PIN.'},status_code=400)
            photo=sanitize_photo(d.get('epod_photo_url'))
            cod=money(d.get('cod_collected') or 0)
            require(cod==money(o['cod_amount']),'COD collection must equal the amount due.')
            if o['payment_method']=='Online':require(o['payment_status']=='paid','Online payment is not confirmed.',409)
            o.update(epod_photo_url=photo,cod_collected=cod/100,delivered_time=now(),status='Delivered',
                payment_status='cod_collected' if o['payment_method']=='COD' else 'paid')
            from workforce import record_delivery_earnings
            record_delivery_earnings(s,o)
            for inv in s['invoices'].values():
                if inv['order_id']==o['id']:inv['status']='paid'
        else:
            require(status!='Failed' or d.get('failure_reason'),'Failure reason is required.')
            transitions={'Assigned':{'Picked Up','In Transit','Arrived','Failed'},'Picked Up':{'In Transit','Arrived','Failed'},'In Transit':{'Arrived','Failed'},'Arrived':{'Failed'}}
            require(status in transitions.get(o['status'],set()),'Invalid delivery status transition.',409)
            o.update(status=status,notes=clean_text(d.get('notes','')),failure_reason=clean_text(d.get('failure_reason','')))
        notify(s,o['owner'],'Delivery updated',f"{o['id']}: {o['status']}")
        from experience import queue_order_email
        event={'Picked Up':'shipped','In Transit':'out_for_delivery','Arrived':'out_for_delivery','Delivered':'delivered'}.get(status)
        if event:
            if event=='out_for_delivery':queue_order_email(s,o,'shipped')
            queue_order_email(s,o,event)
        audit(s,u['email'],'DELIVERY_'+status.upper(),o['id']);return {'message':'Delivery updated.','order':order_view(o,True)}
    if p=='/driver/expenses':
        require(active and active['status']!='completed','An active shift is required.',409)
        amount=money(d['amount']);require(amount>0 and d['category'] in {'Fuel','Toll','Parking','Other'},'Select a valid category and positive amount.')
        e=dict(d,id=uid('EXP'),driver_id=u['id'],shift_id=active['id'],amount=amount/100,date=today(),created_at=now(),approval_status='pending')
        s['expenses'][e['id']]=e;audit(s,u['email'],'EXPENSE_RECORDED',e['id']);return {'expense':e,'message':'Expense saved.'}
    if p=='/driver/reconciliation':
        from operations import driver_cash
        return driver_cash(s,u['id'])
    if p=='/driver/sos':
        alert=dict(d,id=uid('SOS'),driver_id=u['id'],driver_name=u['full_name'],created_at=now(),status='open')
        for key,limit in [('gps_lat',90),('gps_lng',180)]:
            if d.get(key) is not None:require(abs(d[key])<=limit,'Invalid GPS coordinate.')
        s['sos'][alert['id']]=alert;audit(s,u['email'],'SOS_RECORDED',alert['id'])
        return {'status':'recorded','message':'SOS recorded in the administration portal. Call emergency services directly for immediate danger.','alert':alert}
    raise HTTPException(404,'Unknown driver action.')


def credit_summary(s,u):
    due=sum(inv['total'] for inv in mine(s,'invoices',u) if inv['status']=='unpaid')
    cfg=s['credit'].get(u['email'],{})
    return dict(credit_limit=0,utilized_credit=due,available_credit=0,autopay_enabled=False,autopay_method='Not configured',
        subscription_tier=cfg.get('plan_tier','Standard'),aging_buckets={'current_0_30':due,'past_due_31_60':0,'past_due_61_plus':0})


def customer_portal_dispatch(s,u,r,d):
    p=r.scope['route'].path;m=r.method;pp=r.path_params
    collection_routes={'/portal/team':('team','team_members'),'/portal/tickets':('tickets','tickets'),'/portal/quotes':('quotes','quotes'),
        '/portal/rma':('rma','claims'),'/portal/documents':('documents','documents'),'/portal/notifications':('notifications','notifications')}
    if m=='GET' and p in collection_routes:
        col,key=collection_routes[p]
        rows=mine(s,col,u)
        if col=='notifications':rows=[n for n in rows if n.get('portal','customer')=='customer']
        return {key:rows}
    if p=='/portal/auth/2fa':
        require(d.get('enabled') is True,'Email OTP remains required for every sign-in.',400)
        return {'message':'Email OTP is required for every sign-in.','enabled':True}
    if p=='/portal/company-profile':
        if m=='PATCH':
            require(d.get('preferred_currency','INR')=='INR','Transactions use INR.')
            s['profiles'].setdefault(u['email'],{}).update(d);audit(s,u['email'],'COMPANY_PROFILE_UPDATED')
        return {'profile':s['profiles'].get(u['email'],{}),'user':public_user(u)}
    if p=='/portal/team' and m=='POST':
        require(d['role'] in {'account_admin','buyer','finance_viewer','support_agent'},'Invalid team role.')
        # A contact invitation record has no authority over another user's account.
        item=dict(d,id=uid('TEAM'),owner=u['email'],status='invited',permissions=[])
        s['team'][item['id']]=item;audit(s,u['email'],'TEAM_CONTACT_INVITED',item['id']);return {'member':item,'message':'Team contact recorded. It does not grant access to other accounts.'}
    if p=='/portal/tickets' and m=='POST':
        require(d['subject'].strip() and d['description'].strip(),'Subject and description are required.')
        for ident in d.get('attachments',[]):
            owned(s,'uploads',ident,u)
        t=dict(d,id=uid('TKT'),owner=u['email'],customer_email=u['email'],summary=d['subject'],status='open',created_at=now(),history=[])
        s['tickets'][t['id']]=t;audit(s,u['email'],'TICKET_CREATED',t['id']);return {'ticket':t,'message':'Support request saved.'}
    if p=='/portal/tickets/{ticket_id}/reply':
        t=owned(s,'tickets',pp['ticket_id'],u);msg=clean_text(d['message']);require(msg,'Reply is empty.')
        t['history'].append({'sender':'customer','message':msg,'timestamp':now()});audit(s,u['email'],'TICKET_REPLY',t['id']);return {'ticket':t}
    if p=='/portal/knowledge-base':
        articles=[{'id':'KB-1','category':'Orders','type':'Guide','title':'Ordering and delivery PINs','summary':'Choose products and a delivery address. Stock is reserved after checkout. Give your four-digit delivery PIN only to the assigned driver at delivery.'},
            {'id':'KB-2','category':'Billing','type':'Guide','title':'INR payments and invoices','summary':'Cash on delivery and configured Razorpay checkout are supported. Payment is recorded only after provider verification or confirmed COD collection.'},
            {'id':'KB-3','category':'Security','type':'Guide','title':'Account security','summary':'Use a unique password. Every sign-in requires a six-digit email code. New drivers require administrator approval.'}]
        q=r.query_params.get('q','').lower();return {'articles':[a for a in articles if q in (a['title']+' '+a['summary']).lower()]}
    if p=='/portal/ai-assistant':
        if d.get('escalate_to_human'):
            ticket=dict(id=uid('TKT'),owner=u['email'],customer_email=u['email'],category='support',subject='Chat escalation',summary='Chat escalation',description=clean_text(d['message']),status='open',history=[],created_at=now())
            s['tickets'][ticket['id']]=ticket;audit(s,u['email'],'CHAT_ESCALATED',ticket['id'])
            return {'reply':'A support ticket was created: '+ticket['id']+'. An agent has not yet joined.','ticket':ticket}
        # Preserve the original customer chatbot screen and update its source
        # automatically when staff publish approved company knowledge.
        from portal_features import assistant_answer, company_info
        return assistant_answer(clean_text(d.get('message',''),1200),company_info(s))
    if p=='/portal/forum':
        if m=='GET':return {'posts':list(s['forum'].values())}
        item=dict(d,id=uid('FORUM'),owner=u['email'],votes=0,created_at=now());require(item['title'].strip() and item['body'].strip(),'Title and message are required.')
        s['forum'][item['id']]=item;audit(s,u['email'],'FORUM_POSTED',item['id']);return {'post':item}
    if p=='/portal/forum/{post_id}/vote':
        post=s['forum'].get(pp['post_id']);require(post,'Post not found.',404)
        key=u['id']+':'+post['id'];require(key not in s['votes'],'You already voted.',409)
        s['votes'][key]=True;post['votes']+=1;return {'post':post}
    if p=='/portal/catalog/contract-pricing':return {'products':[v for v in s['products'].values() if not v.get('deleted')],'currency':'INR'}
    if p=='/portal/quotes' and m=='POST':
        require(d['items'],'Choose at least one product.')
        lines=[]
        for line in d['items']:
            prod=s['products'].get(str(line.get('product_id','')));require(prod and not prod.get('deleted'),'Choose a catalog product for the quote.')
            qty=line.get('quantity');require(type(qty) is int and qty>0,'Quote quantity must be a positive integer.')
            lines.append(dict(product_id=prod['id'],name=prod['name'],quantity=qty,price=prod['price']))
        item=dict(d,id=uid('QUOTE'),owner=u['email'],items=lines,title='Dairy supply quote',total_amount=sum(money(l['price'])*l['quantity'] for l in lines)/100,status='pending_approval',valid_until=(datetime.now(IST).date()+timedelta(days=7)).isoformat())
        s['quotes'][item['id']]=item;audit(s,u['email'],'QUOTE_REQUESTED',item['id']);return {'quote':item}
    if p=='/portal/quotes/{quote_id}/approve-to-order':
        q=owned(s,'quotes',pp['quote_id'],u)
        require(q['status']=='pending_approval','Quote already converted.',409)
        require(q['valid_until']>=today(),'Quote has expired.',409)
        require(d['signer_name'].strip() and d['signature_hash'].strip(),'Approval name is required.')
        body=Checkout(items=[CartLine(product_id=l['product_id'],quantity=l['quantity']) for l in q['items']],payment_method='COD')
        order=make_order(s,u,body,'quote_'+q['id']);q.update(status='converted_to_order',signed_by=d['signer_name'],converted_order_id=order['id'])
        audit(s,u['email'],'QUOTE_CONVERTED',q['id']);return {'quote':q,'order':order_view(order),'message':'Quote converted using current catalog prices.'}
    if p=='/portal/rma' and m=='POST':
        o=owned(s,'orders',d['order_id'],u);require(d['reason'].strip(),'A reason is required.')
        item=dict(d,id=uid('RMA'),owner=u['email'],user_email=u['email'],amount=o['total_amount'],status='under_review',created_at=now())
        s['rma'][item['id']]=item;audit(s,u['email'],'RMA_SUBMITTED',item['id']);return {'claim':item,'message':'Claim saved for review.'}
    if p=='/portal/billing/invoices':return {'invoices':mine(s,'invoices',u),'credit_account':credit_summary(s,u)}
    if p=='/portal/billing/pay':
        inv=owned(s,'invoices',d['invoice_id'],u);o=owned(s,'orders',inv['order_id'],u)
        require(money(d['amount'])==o['total_paise'],'Invoice amount mismatch.')
        require(o['payment_method']=='Online','This invoice is payable on delivery.',409)
        return {'order_id':o['id'],'payment_required':True,'message':'Continue through verified online checkout.'}
    if p=='/portal/billing/credit-subscription':
        require(d.get('requested_limit') is None,'Credit limits require finance approval.',403)
        require(d.get('plan_tier') in {'Standard','Enterprise Silver','Enterprise Gold','Enterprise Platinum'},'Invalid subscription tier.')
        s['credit'].setdefault(u['email'],{})['plan_tier']=d['plan_tier'];audit(s,u['email'],'PLAN_PREFERENCE_UPDATED')
        return {'credit_account':credit_summary(s,u),'message':'Plan preference saved. No automatic charge was made.'}
    if p=='/portal/notifications/mark-read':
        for n in mine(s,'notifications',u):
            if n.get('portal','customer')=='customer':n['read']=True
        return {'message':'Notifications marked read.'}
    if p=='/portal/surveys':
        require(1<=d['csat_score']<=5 and 0<=d['nps_score']<=10,'Invalid survey score.')
        item=dict(d,id=uid('SURVEY'),owner=u['email'],created_at=now());s['surveys'][item['id']]=item;audit(s,u['email'],'SURVEY_SUBMITTED',item['id']);return {'survey':item,'message':'Feedback saved.'}
    if p=='/portal/enterprise/system-status':
        return {'erp_crm_integrations':[{'key':key,'connector':name,'status':'not_configured','last_sync':None,'entities':'Requires integration setup'} for key,name in [('sap','SAP'),('dynamics','Microsoft Dynamics'),('salesforce','Salesforce'),('quickbooks','QuickBooks')]],
            'white_label':s['branding'].get(u['email'],{}),'portal_analytics':{'orders':len(mine(s,'orders',u)),'tickets':len(mine(s,'tickets',u))},
            'security_audit_logs':[a for a in s['audit'] if a['actor']==u['email']][-30:]}
    if p=='/portal/enterprise/erp-sync':raise HTTPException(503,'ERP connector credentials and an integration adapter have not been configured.')
    if p=='/portal/enterprise/white-label':
        require(re.fullmatch('#[0-9A-Fa-f]{6}',d['primary_color']) and re.fullmatch('#[0-9A-Fa-f]{6}',d['secondary_color']),'Invalid branding color.')
        require(d['font_family'] in {'Inter','Arial','Roboto','Georgia','sans-serif'},'Choose an available font.')
        require(re.fullmatch('[a-zA-Z0-9.-]{1,253}',d['custom_domain']),'Invalid domain.')
        require(not d['company_logo_url'] or d['company_logo_url'].startswith('https://'),'Logo must use HTTPS.')
        s['branding'][u['email']]=d;audit(s,u['email'],'BRANDING_PREFERENCES_SAVED');return {'white_label':d,'message':'Branding preferences saved. Domain/DNS provisioning requires separate setup.'}
    raise HTTPException(404,'Unknown customer portal action.')


class RequestBodyTooLarge(Exception): pass

class BodyLimitMiddleware:
    def __init__(self,app):self.app=app
    async def __call__(self,scope,receive,send):
        if scope['type']!='http':return await self.app(scope,receive,send)
        total=0;started=False
        async def limited_receive():
            nonlocal total
            message=await receive()
            if message['type']=='http.request':
                total+=len(message.get('body',b''))
                if total>3000000:raise RequestBodyTooLarge()
            return message
        async def tracked_send(message):
            nonlocal started
            if message['type']=='http.response.start':started=True
            await send(message)
        try:await self.app(scope,limited_receive,tracked_send)
        except RequestBodyTooLarge:
            if started:raise
            await JSONResponse({'detail':'Request is too large.'},status_code=413)(scope,receive,send)


def install(app, legacy):
    """Replace active routes, retain original request-model schemas in OpenAPI."""
    from fastapi.exceptions import RequestValidationError
    @app.exception_handler(RequestValidationError)
    async def invalid_request(request,exc):
        return JSONResponse({'detail':[{'loc':list(e['loc']),'msg':e['msg'],'type':e['type']} for e in exc.errors()]},status_code=422)
    original_routes=[r for r in app.routes if isinstance(r,APIRoute)]
    original_schema=app.openapi()
    app.router.routes[:]=[r for r in app.routes if not isinstance(r,APIRoute)]
    app.openapi_schema=None
    app.version='8.0.0'
    app.description='First Milk Venture: India / Kerala / INR. Individual password and email OTP accounts; persistent transactional operations.'
    public={'/','/config','/auth/signup','/auth/signin','/auth/verify-otp','/auth/request-otp',
        '/auth/password-reset/request','/auth/password-reset/confirm','/portal/auth/password-reset/request','/portal/auth/password-reset/confirm',
        '/portal/auth/sso/initiate','/admin/rbac/verify','/driver/verify-key'}
    origins=[x.strip() for x in os.getenv('ALLOWED_ORIGINS',','.join(legacy['ALLOWED_ORIGINS'])).split(',') if x.strip()]
    require('*' not in origins,'Use explicit ALLOWED_ORIGINS, never a wildcard.')
    # Replace earlier CORS settings. No cookies or browser-supplied shared role keys are used.
    from fastapi.middleware.cors import CORSMiddleware
    app.user_middleware=[]
    app.add_middleware(CORSMiddleware,allow_origins=origins,allow_credentials=False,
        allow_methods=['GET','POST','PATCH','DELETE','OPTIONS'],allow_headers=['Authorization','Content-Type','Idempotency-Key'],expose_headers=['Content-Disposition','Retry-After','X-Server-Time','X-Resend-Available-At','X-OTP-Expires-At'])

    @app.middleware('http')
    async def security_headers(request,call_next):
        response=None
        size=request.headers.get('content-length')
        if size and (not size.isdigit() or int(size)>3000000):response=JSONResponse({'detail':'Request is too large.'},status_code=413)
        if request.method not in {'GET','HEAD','OPTIONS'} and request.headers.get('origin'):
            origin=request.headers['origin'];own=str(request.base_url).rstrip('/')
            if origin not in origins and origin!=own:response=JSONResponse({'detail':'Origin is not allowed.'},status_code=403)
        if response is None:
            try:
                ip=request.client.host if request.client else 'unknown'
                # Do not trust a client-supplied X-Forwarded-For header.
                await asyncio.to_thread(rate_limit,'ip:'+ip,600,60)
                if request.url.path in public and request.method=='POST':await asyncio.to_thread(rate_limit,'auth:'+ip,40,300)
                if request.url.path in {'/auth/signup','/auth/signin','/portal/auth/password-reset/request','/auth/password-reset/request'}:
                    raw=await request.body()
                    try:
                        parsed=json.loads(raw);identifier=str(parsed.get('email') or parsed.get('identifier') or '').lower()
                        await asyncio.to_thread(rate_limit,'auth-account:'+hashlib.sha256(identifier.encode()).hexdigest(),8,300)
                    except (ValueError,AttributeError):pass
                if request.url.path.startswith(('/admin/','/driver/')) and request.url.path not in public and request.method!='OPTIONS' and not request.headers.get('authorization','').startswith('Bearer '):
                    actor({'users':{},'sessions':{}},request)
                response=await call_next(request)
            except HTTPException as e:response=JSONResponse({'detail':e.detail},status_code=e.status_code)
        response.headers.update({'X-Content-Type-Options':'nosniff','Referrer-Policy':'same-origin','X-Frame-Options':'DENY',
            'Permissions-Policy':'camera=(self), geolocation=(self), microphone=(self)',
            'Content-Security-Policy':"default-src 'self'; script-src 'self' https://checkout.razorpay.com; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdn.jsdelivr.net https://cdnjs.cloudflare.com; font-src 'self' https://fonts.gstatic.com https://cdnjs.cloudflare.com; img-src 'self' data: blob: https:; connect-src 'self' "+' '.join(origins)+" https://api.razorpay.com https://cdn.tailwindcss.com; frame-src https://api.razorpay.com https://checkout.razorpay.com; media-src 'self' blob: https://backend.firstmilkventure.com; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"})
        if not request.url.path.startswith(('/customer/','/driver-portal/','/admin-portal/','/media/')):response.headers['Cache-Control']='no-store'
        if PRODUCTION:response.headers['Strict-Transport-Security']='max-age=31536000; includeSubDomains'
        return response

    def register(path,methods,model=None,tags=None):
        def endpoint(request,body=None):
            if path in {'/', '/config'} and request.method=='GET':
                # Public deployment metadata does not depend on customers or orders.
                # The shared IP limit and response security headers still apply.
                return dispatch({}, request, body)
            if path in {'/auth/signup','/auth/signin','/auth/request-otp','/auth/password-reset/request','/portal/auth/password-reset/request'}:
                return challenge_request(request,body)
            context=read_transaction if request.method in {'GET','HEAD'} else transaction
            with context() as s:
                if request.method not in {'GET','HEAD'}:expire_reservations(s)
                return dispatch(s,request,body)
        endpoint.__name__='secure_'+methods[0].lower()+'_'+re.sub(r'[^a-zA-Z0-9]+','_',path).strip('_')
        parameters=[inspect.Parameter('request',inspect.Parameter.POSITIONAL_OR_KEYWORD,annotation=Request)]
        if model:parameters.append(inspect.Parameter('body',inspect.Parameter.POSITIONAL_OR_KEYWORD,annotation=model))
        endpoint.__signature__=inspect.Signature(parameters)
        app.add_api_route(path,endpoint,methods=methods,tags=tags or ['Secure Operations'])
    overrides={'/auth/signup':Signup,'/auth/signin':Signin,'/auth/request-otp':OTPRequest,'/auth/verify-otp':OTPVerify,
        '/orders/checkout':Checkout,'/admin/products/{product_id}/stock':StockUpdate}
    seen=set()
    for route in original_routes:
        model=overrides.get(route.path)
        if not model and route.dependant.body_params:model=route.dependant.body_params[0].field_info.annotation
        register(route.path,sorted(route.methods),model,route.tags);seen.add((route.path,next(iter(route.methods))))
    additions=[('/config','GET',None),('/auth/session','GET',None),('/auth/logout','POST',None),('/sync','GET',None),('/orders','GET',None),
        ('/orders/{order_id}/cancel','POST',None),('/subscriptions/{subscription_id}','PATCH',EmptyBody),
        ('/payments/create','POST',EmptyBody),('/payments/verify','POST',PaymentVerify),('/admin/deliveries','GET',None),
        ('/admin/generate-deliveries','POST',EmptyBody),('/admin/attendance','GET',None),('/admin/sos','GET',None),
        ('/admin/drivers/{driver_id}/approve','POST',None),('/admin/invitations','POST',EmptyBody),('/driver/shifts','GET',None),
        ('/auth/password-reset/request','POST',EmptyBody),('/auth/password-reset/confirm','POST',EmptyBody)]
    for path,method,model in additions:register(path,[method],model)

    @app.post('/webhooks/payment',tags=['Verified Payments'])
    async def webhook(request:Request):
        raw=await request.body();secret=os.getenv('RAZORPAY_WEBHOOK_SECRET','')
        require(bool(secret),'Payment webhook is not configured.',503)
        require(hmac.compare_digest(hmac.new(secret.encode(),raw,'sha256').hexdigest(),request.headers.get('x-razorpay-signature','')),'Invalid webhook signature.',401)
        try:payload=json.loads(raw)
        except ValueError:raise HTTPException(400,'Invalid event body.')
        event_id=request.headers.get('x-razorpay-event-id') or hashlib.sha256(raw).hexdigest()
        with transaction() as s:
            if event_id in s['events']:return {'status':'success','duplicate':True}
            if payload.get('event')=='payment.captured':
                payment=payload.get('payload',{}).get('payment',{}).get('entity',{})
                order=next((o for o in s['orders'].values() if o.get('provider_order_id')==payment.get('order_id')),None)
                require(order is not None,'Order not found.',404)
                payment_captured(s,order,payment.get('id',''))
            s['events'][event_id]=now()
        return {'status':'success'}

    @app.get('/portal/billing/invoices/{invoice_id}/download',tags=['Invoice exports'])
    def download_invoice(invoice_id:str,request:Request):
        with read_transaction() as s:
            u,_=actor(s,request,'customer');inv=owned(s,'invoices',invoice_id,u);o=owned(s,'orders',inv['order_id'],u)
            # Accurate machine-readable sales invoice; statutory e-invoice integration is not claimed.
            return JSONResponse(dict(inv,items=o['items'],subtotal_paise=o['subtotal_paise'],tax_paise=o['tax_paise'],total_paise=o['total_paise'],seller='First Milk Venture',country='India',state='Kerala'),
                headers={'Content-Disposition':'attachment; filename="'+inv['id']+'.json"'})

    @app.get('/portal/billing/statement',tags=['Invoice exports'])
    def statement(request:Request):
        import csv
        with read_transaction() as s:
            u,_=actor(s,request,'customer');out=io.StringIO();writer=csv.writer(out)
            writer.writerow(['Invoice','Date','Currency','Amount','Status'])
            for inv in mine(s,'invoices',u):writer.writerow([inv['id'],inv['date'],'INR',inv['total'],inv['status']])
            return Response(out.getvalue(),media_type='text/csv',headers={'Content-Disposition':'attachment; filename="statement.csv"'})

    @app.get('/admin/attendance/export',tags=['Attendance exports'])
    def attendance_export(request:Request):
        import csv
        with read_transaction() as s:
            actor(s,request,'admin','drivers');out=io.StringIO();w=csv.writer(out)
            w.writerow(['Driver','Clock in','Clock out','Work seconds','Break seconds','Vehicle'])
            month=request.query_params.get('month',today()[:7])
            require(bool(re.fullmatch(r'\d{4}-\d{2}',month)),'Month must be YYYY-MM.')
            for sh in s['shifts'].values():
                if sh['clock_in'].startswith(month):
                    t=shift_times(sh);name=sh['driver_name'];name="'"+name if name.startswith(('=','+','-','@')) else name
                    plate=sh['vehicle_plate'];plate="'"+plate if plate.startswith(('=','+','-','@')) else plate
                    w.writerow([name,sh['clock_in'],sh.get('clock_out',''),t['work_seconds'],t['break_duration_seconds'],plate])
            return Response(out.getvalue(),media_type='text/csv',headers={'Content-Disposition':'attachment; filename="attendance-'+month+'.csv"'})

    # Explicit static allowlists prevent exposing backend files, database, requirements screenshots or credentials.
    for url,folder in [('/customer','Customer Portal'),('/admin-portal','Admin Portal'),('/driver-portal','Delivery Driver Portal')]:
        def file_handler(target):
            def serve():
                if not target.is_file():
                    raise HTTPException(404, 'Portal asset not found.')
                return FileResponse(target)
            return serve
        app.add_api_route(url+'/',file_handler(ROOT.parent/folder/'index.html'),methods=['GET'],include_in_schema=False)
        for filename in ['index.html','config.js','portal-core.js','portal.css','tailwind.css','design-system.css','portal-theme.css','polish.css','app.js','admin.js','driver.js','experience.js','experience.css','operations.js','operations.css','cash.js','cash.css','workspace.js','workspace.css','workforce.js','workforce.css','orders.js','orders.css']:
            target=ROOT.parent/folder/filename
            if target.exists():
                app.add_api_route(url+'/'+filename,file_handler(target),methods=['GET'],include_in_schema=False)
    @app.get('/portal/billing/invoices/{invoice_id}/pdf',tags=['Invoice exports'])
    def invoice_pdf(invoice_id:str,request:Request):
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,Table,TableStyle
        from xml.sax.saxutils import escape
        with transaction() as state:
            u,_=actor(state,request,'customer');inv=owned(state,'invoices',invoice_id,u);order=owned(state,'orders',inv['order_id'],u)
            output=io.BytesIO();pdf=SimpleDocTemplate(output,pagesize=A4,title='First Milk Venture sales invoice',leftMargin=40,rightMargin=40)
            styles=getSampleStyleSheet();story=[Paragraph('FIRST MILK VENTURE',styles['Title']),Paragraph('Sales Invoice · Kerala, India · INR',styles['Normal']),Spacer(1,18)]
            for label,value in [('Invoice',inv['id']),('Date',inv['date']),('Customer',u['full_name']),('Email',u['email']),('Delivery address',order['delivery_address']),('GSTIN',inv.get('gstin') or 'Not supplied')]:
                story.append(Paragraph(escape(label+': '+str(value)),styles['Normal']))
            story.append(Spacer(1,20));rows=[['Product','Quantity','Unit INR','Amount INR']]
            for line in order['items']:rows.append([Paragraph(escape(line['name']),styles['Normal']),str(line['quantity']),format(line['price'],'.2f'),format(line['amount_paise']/100,'.2f')])
            table=Table(rows,colWidths=[240,60,90,90],repeatRows=1)
            table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#e7f2eb')),('GRID',(0,0),(-1,-1),.5,colors.HexColor('#c6d9cd')),('VALIGN',(0,0),(-1,-1),'TOP'),('BOTTOMPADDING',(0,0),(-1,-1),9),('TOPPADDING',(0,0),(-1,-1),9)]))
            story.extend([table,Spacer(1,18)])
            for label,value in [('Subtotal',order['subtotal_paise']),('GST',order['tax_paise']),('Total',order['total_paise'])]:story.append(Paragraph(label+': INR '+format(value/100,'.2f'),styles['Normal']))
            story.extend([Spacer(1,12),Paragraph('Payment status: '+escape(inv['status']),styles['Normal']),Paragraph('This is a sales invoice. Statutory e-invoice/IRN integration is a separate configuration.',styles['Normal'])])
            pdf.build(story);audit(state,u['email'],'INVOICE_PDF_DOWNLOADED',inv['id'])
            return Response(output.getvalue(),media_type='application/pdf',headers={'Content-Disposition':'attachment; filename="'+inv['id']+'.pdf"'})

    def file_payload(data):
        name=clean_text(data.get('filename'),150);mime=data.get('mime_type','')
        require(name and '/' not in name and "\\" not in name,'Invalid filename.',422)
        require(mime in {'application/pdf','image/png','image/jpeg','text/plain'},'Allowed files: PDF, PNG, JPEG and plain text.',422)
        try:raw=base64.b64decode(data.get('data_base64',''),validate=True)
        except Exception:raise HTTPException(422,'Invalid file encoding.')
        require(0<len(raw)<=1500000,'File must be between 1 byte and 1.5 MB.',422)
        if mime=='application/pdf':require(raw.startswith(b'%PDF-'),'Invalid PDF.',422)
        if mime=='image/png':require(raw.startswith(b'\x89PNG\r\n\x1a\n'),'Invalid PNG.',422)
        if mime=='image/jpeg':require(raw.startswith(b'\xff\xd8'),'Invalid JPEG.',422)
        if mime=='text/plain':
            try:raw.decode('utf-8')
            except UnicodeDecodeError:raise HTTPException(422,'Text files must be UTF-8.')
        return name,mime,raw

    @app.post('/portal/uploads',tags=['Private files'])
    def upload_attachment(request:Request,body:EmptyBody):
        with transaction() as state:
            u,_=actor(state,request,'customer');name,mime,raw=file_payload(body.model_dump())
            state.setdefault('uploads',{})
            require(len(mine(state,'uploads',u))<50,'Maximum private upload count reached.',409)
            item=dict(id=uid('FILE'),owner=u['email'],filename=name,mime_type=mime,data_base64=base64.b64encode(raw).decode(),created_at=now())
            state['uploads'][item['id']]=item;audit(state,u['email'],'ATTACHMENT_UPLOADED',item['id'])
            return {k:v for k,v in item.items() if k!='data_base64'}

    @app.post('/admin/documents',tags=['Private files'])
    def share_document(request:Request,body:EmptyBody):
        with transaction() as state:
            u,_=actor(state,request,'admin','support');d=body.model_dump();name,mime,raw=file_payload(d)
            email=str(TypeAdapter(EmailStr).validate_python(d.get('customer_email',''))).lower()
            require('customer:'+email in state['users'],'Customer not found.',404)
            state.setdefault('uploads',{});file_id=uid('FILE')
            state['uploads'][file_id]=dict(id=file_id,owner=email,filename=name,mime_type=mime,data_base64=base64.b64encode(raw).decode(),created_at=now())
            item=dict(id=uid('DOC'),file_id=file_id,owner=email,title=clean_text(d.get('title') or name,150),category=clean_text(d.get('category') or 'Shared documents',150),status='Shared',created_at=now())
            state['documents'][item['id']]=item;notify(state,email,'Document shared',item['title']);audit(state,u['email'],'DOCUMENT_SHARED',item['id'])
            return {'document':item,'message':'Document shared with this customer only.'}

    @app.get('/portal/files/{file_id}',tags=['Private files'])
    def private_file(file_id:str,request:Request):
        with read_transaction() as state:
            u,_=actor(state,request,'customer');item=owned(state,'uploads',file_id,u)
            # Generated IDs avoid filename/header injection; download disposition prevents inline HTML execution.
            from urllib.parse import quote
            return Response(base64.b64decode(item['data_base64']),media_type=item['mime_type'],headers={'Content-Disposition':"attachment; filename*=UTF-8''"+quote(item['filename'],safe='')})

    @app.get('/admin/deliveries/{delivery_id}/proof',tags=['Delivery proof'])
    def delivery_proof(delivery_id:str,request:Request):
        with read_transaction() as state:
            actor(state,request,'admin','orders');order=state['orders'].get(delivery_id)
            require(order and order.get('epod_photo_url'),'Proof photograph not found.',404)
            return Response(base64.b64decode(order['epod_photo_url'].split(',',1)[1]),media_type='image/jpeg',headers={'Content-Disposition':'attachment; filename="proof.jpg"'})

    from fastapi.openapi.utils import get_openapi
    def schema():
        if app.openapi_schema is None:
            result=get_openapi(title=app.title,version=app.version,description=app.description,routes=app.routes)
            result.setdefault('components',{}).setdefault('schemas',{}).update({k:v for k,v in original_schema.get('components',{}).get('schemas',{}).items() if k not in result.get('components',{}).get('schemas',{})})
            result['info']['x-retained-original-schemas']=True
            app.openapi_schema=result
        return app.openapi_schema
    app.openapi=schema
    app.add_middleware(BodyLimitMiddleware)
    with transaction() as s:
        for key,value in initial_state().items():s.setdefault(key,value)
        s['sessions']={k:v for k,v in s['sessions'].items() if v['expires']>time.time()}
        from bootstrap_admin import configure_first_admin_invitation
        configure_first_admin_invitation(s)
    try:os.chmod(DB_PATH,0o600)
    except OSError:pass
    from experience import install_experience
    install_experience(app)

    from operations import install_operations
    install_operations(app)

    from workforce import install_workforce
    install_workforce(app)

    # Additive customer/staff tools. Existing API schemas and routes stay intact.
    from portal_features import install_portal_features
    install_portal_features(app)
    app.version='13.1.0'
    from starlette.middleware.gzip import GZipMiddleware
    class APICompression:
        def __init__(self, app):
            self.app=app
            self.compressed=GZipMiddleware(app,minimum_size=1000,compresslevel=5)
        async def __call__(self, scope, receive, send):
            # JPEG/audio/video are already compressed. Do not spend CPU or add
            # buffering latency on private chat playback or public ad assets.
            path=scope.get('path','')
            binary=path.startswith(('/media/','/order-chat-media/','/driver-documents/')) or path.endswith('/proof')
            await (self.app if binary else self.compressed)(scope,receive,send)
    app.add_middleware(APICompression)
