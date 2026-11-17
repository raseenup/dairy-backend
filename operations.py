"""Portal fixes, catalog media, scoped snapshots and auditable cash handovers.

All financial mutations use the existing serializable state transaction. Public
catalog images live outside the state row so polling never transfers image bytes.
"""
from __future__ import annotations
import base64
import hashlib
import io
import json
import sqlite3
from types import SimpleNamespace

from fastapi import HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from PIL import Image


def core():
    import secure_backend
    return secure_backend


def driver_cash(state, driver_id):
    c = core()
    orders = [o for o in state['orders'].values() if o.get('assigned_driver_id') == driver_id
              and o['status'] == 'Delivered' and o['payment_status'] == 'cod_collected']
    expenses = [e for e in state['expenses'].values() if e['driver_id'] == driver_id]
    receipts = [r for r in state.get('cash_receipts', {}).values() if r['driver_id'] == driver_id]
    handovers = [r for r in state.get('cash_handovers', {}).values() if r['driver_id'] == driver_id]
    collected = sum(c.money(o['cod_collected']) for o in orders)
    approved = sum(c.money(e['amount']) for e in expenses if e.get('approval_status', 'pending') == 'approved')
    pending = sum(c.money(e['amount']) for e in expenses if e.get('approval_status', 'pending') == 'pending')
    received = sum(r['amount_paise'] for r in receipts)
    outstanding = collected - approved - received
    pending_handover = sum(r['amount_paise'] for r in handovers if r['status'] == 'pending')
    day = c.today()
    return dict(currency='INR', total_cod_collected=sum(c.money(o['cod_collected']) for o in orders
                if str(o.get('delivered_time', '')).startswith(day))/100,
        total_expenses=sum(c.money(e['amount']) for e in expenses if e['date'] == day)/100,
        lifetime_cod_collected=collected/100, approved_expenses=approved/100, pending_expenses=pending/100,
        cash_received=received/100, net_cash_balance=outstanding/100,
        net_cash_to_remit=max(0, outstanding)/100, reimbursement_due=max(0, -outstanding)/100,
        pending_handover=pending_handover/100, available_to_submit=max(0, outstanding-pending_handover)/100,
        expenses=expenses, receipts=receipts, handovers=handovers)


def order_summary(state):
    groups = dict(unassigned=0, assigned=0, cancelled=0, completed=0, awaiting_payment=0, failed=0)
    for order in state['orders'].values():
        key = {'Unassigned':'unassigned', 'Cancelled':'cancelled', 'Delivered':'completed',
               'Awaiting Payment':'awaiting_payment', 'Failed':'failed'}.get(order['status'], 'assigned')
        groups[key] += 1
    return dict(groups, total=sum(groups.values()))


def scoped_request(request, path):
    return SimpleNamespace(scope={'route':SimpleNamespace(path=path)}, method='GET',
                           path_params={}, query_params=request.query_params)


def sync_status(request:Request):
    """Read only this session, user and revision, excluding the full state body."""
    c = core()
    import jwt
    authorization = request.headers.get('authorization', '')
    if not authorization.startswith('Bearer '):
        c.actor({'users':{}, 'sessions':{}}, request)
    try:
        claims = jwt.decode(authorization[7:], c.SECRET, algorithms=['HS256'],
            issuer='first-milk-venture', audience='fmv-portals',
            options={'require':['exp','iat','sub','jti','portal']})
    except jwt.InvalidTokenError:
        raise HTTPException(401, 'Session expired or invalid. Sign in again.')
    key = claims['portal'] + ':' + claims['sub']
    if c.DATABASE_URL:
        with c.postgres_connect() as conn:
            row = conn.execute("SELECT data->'revision', data->'users'->%s, data->'sessions'->%s "
                               'FROM fmv_secure.application_state WHERE id=1', (key, claims['jti'])).fetchone()
    else:
        with sqlite3.connect(c.DB_PATH, timeout=10) as conn:
            row = conn.execute("SELECT json_extract(data,'$.revision'), "
                "(SELECT value FROM json_each(data,'$.users') WHERE key=?), "
                "(SELECT value FROM json_each(data,'$.sessions') WHERE key=?) "
                'FROM application_state WHERE id=1', (key, claims['jti'])).fetchone()
            if row:row = (row[0], json.loads(row[1]) if row[1] else None, json.loads(row[2]) if row[2] else None)
    c.require(row, 'Session expired. Sign in again.', 401)
    projected = dict(users={key:row[1]}, sessions={claims['jti']:row[2]})
    user, _ = c.actor(projected, request)
    return dict(revision=row[0], portal=user['portal'], server_time=c.now())


def finance_overview(state, user, request):
    result = core().admin_dispatch(state, user, scoped_request(request, '/admin/finance/overview'), {})
    result['driver_cash'] = [dict(driver_id=d['id'], name=d['name'], email=d.get('email', ''),
                                disabled=bool(d.get('deleted')), **driver_cash(state, d['id']))
                             for d in state['drivers'].values() if d.get('email')]
    result['ledger']['driver_cash_received'] = sum(r['amount_paise'] for r in state.get('cash_receipts', {}).values())/100
    return result


class ProductEdit(BaseModel):
    model_config = ConfigDict(extra='forbid')
    version: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=150)
    description: str = Field(max_length=3000)
    unit: str = Field(min_length=1, max_length=100)
    price: float = Field(ge=0, le=1000000, allow_inf_nan=False)
    stock_quantity: int = Field(strict=True, ge=0, le=10000000)
    min_stock_threshold: int = Field(strict=True, ge=0, le=10000000)
    tax_rate: float = Field(ge=0, le=100, allow_inf_nan=False)
    # None preserves an existing external/legacy image, '' removes it.
    media_id: str | None = Field(default=None, max_length=100)


class ProductMedia(BaseModel):
    model_config = ConfigDict(extra='forbid')
    filename: str = Field(min_length=1, max_length=200)
    data_base64: str = Field(min_length=1, max_length=2100000)


def media_connection():
    c = core()
    return c.postgres_connect() if c.DATABASE_URL else sqlite3.connect(c.DB_PATH, timeout=10)


def media_lookup(media_id):
    c = core()
    with media_connection() as conn:
        sql = ('SELECT mime_type, content FROM fmv_secure.product_media WHERE id=%s'
               if c.DATABASE_URL else 'SELECT mime_type, content FROM product_media WHERE id=?')
        return conn.execute(sql, (media_id,)).fetchone()


def validate_product_media(encoded):
    c = core()
    try:
        raw = base64.b64decode(encoded, validate=True)
        c.require(0 < len(raw) <= 1500000, 'Choose a product image or GIF up to 1.5 MB.', 422)
        with Image.open(io.BytesIO(raw)) as image:
            c.require(image.format in {'JPEG','PNG','GIF','WEBP'} and 0 < max(image.size) <= 4096
                      and image.width*image.height <= 12000000,
                      'Use JPEG, PNG, GIF or WebP up to 4096 pixels.', 422)
            frames = getattr(image, 'n_frames', 1)
            c.require(frames <= 300 and image.width*image.height*frames <= 40000000,
                      'This animation is too large. Use fewer frames or a smaller image.', 422)
            mime = Image.MIME[image.format]
            # Validate every frame; retain animation and the original picture.
            for index in range(frames):
                image.seek(index)
                image.load()
        return mime, raw
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(422, 'Invalid product image. Use JPEG, PNG, GIF or WebP.')


class Handover(BaseModel):
    model_config = ConfigDict(extra='forbid')
    amount: float = Field(gt=0, le=100000000, allow_inf_nan=False)
    reference: str = Field(min_length=1, max_length=200)


class CashReceipt(Handover):
    driver_id: str = Field(min_length=1, max_length=100)
    handover_id: str = Field(default='', max_length=100)
    confirmed_received: bool = Field(strict=True)


class ExpenseReview(BaseModel):
    model_config = ConfigDict(extra='forbid')
    status: str = Field(pattern='^(approved|rejected)$')
    note: str = Field(default='', max_length=500)


def idempotent_record(state, collection, request, user, body):
    c = core()
    key = request.headers.get('idempotency-key', '')
    import re
    c.require(bool(re.fullmatch('[A-Za-z0-9_-]{16,100}', key)), 'A valid Idempotency-Key is required.', 422)
    fingerprint = hashlib.sha256(json.dumps(body.model_dump(), sort_keys=True).encode()).hexdigest()
    scoped = user['id'] + ':' + key
    for record in state.setdefault(collection, {}).values():
        if record['request_key'] == scoped:
            c.require(record['fingerprint'] == fingerprint, 'This receipt key was used with different details.', 409)
            return record, scoped, fingerprint
    return None, scoped, fingerprint


def install_operations(app):
    c = core()
    # Additive, idempotent DDL; no table, original record or schema is removed.
    with media_connection() as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS ' + ('fmv_secure.' if c.DATABASE_URL else '') +
            'product_media (id TEXT PRIMARY KEY, mime_type TEXT NOT NULL, content ' +
            ('BYTEA' if c.DATABASE_URL else 'BLOB') + ' NOT NULL)')
    app.version = '10.0.0'

    app.router.routes[:] = [r for r in app.routes if not (getattr(r,'path','') == '/sync'
                          and 'GET' in getattr(r,'methods',set()))]
    app.add_api_route('/sync', sync_status, methods=['GET'], tags=['Portal synchronization'])

    @app.get('/portal/snapshot', tags=['Portal synchronization'])
    def portal_snapshot(request:Request):
        with c.read_transaction() as state:
            user, _ = c.actor(state, request)
            result = dict(revision=state['revision'], user=c.public_user(user))
            if user['portal'] == 'customer':
                from experience import live, public_campaign
                paths = ['team','tickets','knowledge-base','forum','quotes','rma','billing/invoices',
                         'documents','notifications','enterprise/system-status']
                result.update(products=[p for p in state['products'].values() if not p.get('deleted')],
                    orders=[c.order_view(o) for o in c.mine(state, 'orders', user)],
                    subscriptions=c.mine(state, 'subscriptions', user),
                    enterprise=[c.customer_portal_dispatch(state, user, scoped_request(request, '/portal/'+p), {}) for p in paths])
                result['addresses'] = [a for a in state.get('addresses', {}).values() if a['owner'] == user['email']]
                result['content'] = {'campaigns':[public_campaign(state, x) for x in sorted(
                    state.get('campaigns', {}).values(), key=lambda x:(x['sort_order'], x['id']))
                    if live(x) and x['placement'] != 'email']}
            elif user['portal'] == 'driver':
                for name, path in [('profile','profile'), ('active','shifts/active'), ('orders','deliveries'), ('history','shifts')]:
                    result[name] = c.driver_dispatch(state, user, scoped_request(request, '/driver/'+path), {})
                result['cash'] = driver_cash(state, user['id'])
                result['notifications'] = [n for n in c.mine(state, 'notifications', user) if n.get('portal') == 'driver']
            else:
                permissions = c.PERMISSIONS.get(user['role'], set())
                allowed = lambda permission: '*' in permissions or permission in permissions
                if allowed('catalog'):
                    result['products'] = [p for p in state['products'].values() if not p.get('deleted')]
                for permission, name, path in [('orders','orders','deliveries'), ('drivers','drivers','drivers'),
                    ('drivers','attendance','attendance'), ('drivers','sos','sos'),
                    ('support','users','support/users'), ('support','tickets','support/tickets')]:
                    if allowed(permission):result[name] = c.admin_dispatch(state, user, scoped_request(request, '/admin/'+path), {})
                if allowed('orders'):result['order_summary'] = order_summary(state)
                if allowed('finance'):result['finance'] = finance_overview(state, user, request)
            from workforce import extend_snapshot
            extend_snapshot(state,user,result,request)
            return result

    @app.get('/admin/order-summary', tags=['Dispatch updates'])
    def summary(request:Request):
        with c.read_transaction() as state:
            c.actor(state, request, 'admin', 'orders')
            return order_summary(state)

    @app.post('/admin/product-media', tags=['Catalog management'])
    def upload_product_media(request:Request, body:ProductMedia):
        with c.read_transaction(maintain=False) as state:
            user, _ = c.actor(state, request, 'admin', 'catalog')
        mime, raw = validate_product_media(body.data_base64)
        ident = c.uid('PRODUCTMEDIA')
        with media_connection() as conn:
            sql = ('INSERT INTO fmv_secure.product_media VALUES (%s,%s,%s)' if c.DATABASE_URL
                   else 'INSERT INTO product_media VALUES (?,?,?)')
            conn.execute(sql, (ident, mime, raw))
        with c.transaction() as state:
            c.actor(state, request, 'admin', 'catalog')
            state.setdefault('product_media_index',{})[ident] = {'id':ident,'mime_type':mime}
            c.audit(state, user['email'], 'PRODUCT_MEDIA_UPLOADED', ident)
        return dict(id=ident, mime_type=mime, url='/media/products/'+ident)

    @app.get('/media/products/{media_id}', tags=['Public catalog assets'])
    def product_asset(media_id:str):
        item = media_lookup(media_id)
        c.require(item, 'Product image not found.', 404)
        return Response(bytes(item[1]), media_type=item[0], headers={
            'Cache-Control':'public, max-age=31536000, immutable', 'X-Content-Type-Options':'nosniff'})

    @app.patch('/admin/products/{product_id}', tags=['Catalog management'])
    def edit_product(product_id:str, request:Request, body:ProductEdit):
        # Resolve the public asset outside the state-write lock.
        with c.read_transaction(maintain=False) as state:
            c.actor(state, request, 'admin', 'catalog')
        if body.media_id:c.require(media_lookup(body.media_id), 'Upload the product image first.', 404)
        with c.transaction() as state:
            user, _ = c.actor(state, request, 'admin', 'catalog')
            product = state['products'].get(product_id)
            c.require(product and not product.get('deleted'), 'Product not found.', 404)
            c.require(product['version'] == body.version, 'Product changed. Refresh before saving.', 409)
            values = body.model_dump(exclude={'version','media_id'})
            for key, limit in [('name',150), ('description',3000), ('unit',100)]:
                values[key] = c.clean_text(values[key], limit)
            c.require(values['name'] and values['unit'], 'Product name and unit are required.', 422)
            c.money(body.price)
            if body.media_id is not None:
                values.update(media_id=body.media_id, image_url='/media/products/'+body.media_id if body.media_id else '')
            product.update(values, version=product['version']+1)
            c.audit(state, user['email'], 'PRODUCT_DETAILS_UPDATED', product_id)
            return {'product':product, 'message':'Product details, image, stock and price saved.'}

    @app.post('/driver/cash/handovers', tags=['Driver cash'])
    def submit_handover(request:Request, body:Handover):
        with c.transaction() as state:
            user, _ = c.actor(state, request, 'driver')
            existing, key, fingerprint = idempotent_record(state, 'cash_handovers', request, user, body)
            if existing:return {'handover':existing}
            amount = c.money(body.amount)
            c.require(0 < amount <= c.money(driver_cash(state, user['id'])['available_to_submit']),
                      'Amount exceeds the cash available to submit.', 409)
            reference = c.clean_text(body.reference, 200)
            c.require(reference, 'Enter a cash handover reference.', 422)
            item = dict(id=c.uid('HANDOVER'), driver_id=user['id'], amount_paise=amount,
                amount=amount/100, reference=reference, status='pending', created_at=c.now(),
                request_key=key, fingerprint=fingerprint)
            state['cash_handovers'][item['id']] = item
            c.audit(state, user['email'], 'CASH_HANDOVER_SUBMITTED', item['id'])
            return {'handover':item, 'message':'Handover submitted. Your balance clears when accounts confirms receipt.'}

    @app.post('/admin/finance/cash-receipts', tags=['Accounts cash receipts'])
    def receive_cash(request:Request, body:CashReceipt):
        with c.transaction() as state:
            user, _ = c.actor(state, request, 'admin', 'finance')
            existing, key, fingerprint = idempotent_record(state, 'cash_receipts', request, user, body)
            if existing:return {'receipt':existing}
            c.require(body.confirmed_received is True, 'Confirm that accounts has physically received this cash.', 422)
            driver = state['drivers'].get(body.driver_id)
            c.require(driver and driver.get('email'), 'Registered driver not found.', 404)
            amount = c.money(body.amount)
            c.require(0 < amount <= c.money(driver_cash(state, driver['id'])['net_cash_to_remit']),
                      'Cash receipt exceeds the outstanding driver balance.', 409)
            handover = None
            if body.handover_id:
                handover = state.get('cash_handovers', {}).get(body.handover_id)
                c.require(handover and handover['driver_id'] == driver['id'] and handover['status'] == 'pending'
                          and handover['amount_paise'] == amount, 'Handover is closed or the amount does not match.', 409)
            reference = c.clean_text(body.reference, 200)
            c.require(reference, 'Enter the accounts receipt reference.', 422)
            receipt = dict(id=c.uid('RECEIPT'), driver_id=driver['id'], driver_name=driver['name'],
                amount=amount/100, amount_paise=amount, reference=reference, handover_id=body.handover_id,
                received_by=user['email'], received_at=c.now(), request_key=key, fingerprint=fingerprint)
            state['cash_receipts'][receipt['id']] = receipt
            if handover:handover.update(status='received', receipt_id=receipt['id'])
            from experience import queue_email
            queue_email(state, 'cash-receipt:'+receipt['id'], driver['email'], 'cash_receipt', name=driver['name'],
                details=[('Receipt',receipt['id']), ('Amount received','INR %.2f' % receipt['amount']),
                         ('Accounts reference',reference), ('Received by',user['email'])])
            c.notify(state, driver['email'], 'Cash received by accounts',
                     'INR %.2f received. Reference: %s.' % (receipt['amount'], reference), portal='driver')
            c.audit(state, user['email'], 'DRIVER_CASH_RECEIVED', receipt['id'])
            return {'receipt':receipt, 'cash':driver_cash(state, driver['id']), 'message':'Cash receipt saved and driver balance updated.'}

    @app.patch('/admin/finance/expenses/{expense_id}', tags=['Accounts expense review'])
    def review_expense(expense_id:str, request:Request, body:ExpenseReview):
        with c.transaction() as state:
            user, _ = c.actor(state, request, 'admin', 'finance')
            expense = state['expenses'].get(expense_id)
            c.require(expense, 'Expense not found.', 404)
            c.require(expense.get('approval_status','pending') == 'pending', 'Expense has already been reviewed.', 409)
            expense.update(approval_status=body.status, reviewed_by=user['email'], reviewed_at=c.now(), note=c.clean_text(body.note,500))
            c.audit(state, user['email'], 'DRIVER_EXPENSE_'+body.status.upper(), expense_id)
            return {'expense':expense}

    @app.patch('/admin/finance/cash-handovers/{handover_id}/reject', tags=['Accounts cash receipts'])
    def reject_handover(handover_id:str, request:Request):
        with c.transaction() as state:
            user, _ = c.actor(state, request, 'admin', 'finance')
            item = state.get('cash_handovers', {}).get(handover_id)
            c.require(item and item['status'] == 'pending', 'Pending handover not found.', 409)
            item.update(status='rejected', reviewed_by=user['email'], reviewed_at=c.now())
            c.audit(state, user['email'], 'CASH_HANDOVER_REJECTED', handover_id)
            return {'handover':item}

    # Include cash details in the existing finance endpoint, too.
    app.router.routes[:] = [r for r in app.routes if not (getattr(r,'path','') == '/admin/finance/overview'
                          and 'GET' in getattr(r,'methods',set()))]
    @app.get('/admin/finance/overview', tags=['Accounts cash receipts'])
    def finances(request:Request):
        with c.read_transaction() as state:
            user, _ = c.actor(state, request, 'admin', 'finance')
            return finance_overview(state, user, request)
