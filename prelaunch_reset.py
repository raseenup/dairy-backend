"""One-time pre-public-launch reset for *test* dispatch and accounting data.

Never usable merely because a browser displays a button: production must be
explicitly armed by the operator and the authenticated actor must be super admin.
No customer/staff identities, catalog, payroll rules or audit records are erased.
"""
import hashlib
import json
import os
import secrets
import time
from datetime import datetime

from fastapi import Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

RESET_PHRASE = 'RESET FIRST MILK VENTURE TEST DATA'
DISPATCH_KEYS = (
    'orders', 'subscriptions', 'shifts', 'sos', 'delivery_attempts',
    'delivery_ratings', 'return_collections', 'return_yard_reports',
    'return_damage_cases', 'rma', 'driver_incentives', 'incentive_claims',
    'incentive_publish_requests', 'order_chats', 'chat_reads',
    'chat_send_requests', 'chat_attachments', 'chat_media_requests',
)
ACCOUNTING_KEYS = (
    'invoices', 'expenses', 'cash_handovers', 'cash_receipts',
    'driver_earnings', 'driver_payouts', 'earning_requests',
    'credit', 'quotes',
)
EXTRA_KEYS = ('notifications', 'idempotency')
ALL_KEYS = tuple(dict.fromkeys(DISPATCH_KEYS + ACCOUNTING_KEYS + EXTRA_KEYS))
KEEP_KEYS = (
    'users', 'drivers', 'products', 'tax', 'branding', 'invites', 'audit',
    'events', 'driver_pay_rules', 'driver_vehicles', 'driver_documents',
    'addresses', 'sessions', 'challenges', 'campaigns', 'company_pages',
    'tickets', 'uploads',
)


def core():
    import secure_backend
    return secure_backend


def authorized(state, request):
    c = core()
    user, session_id = c.actor(state, request, 'admin')
    c.require(user['role'] == 'super_admin',
              'Only the Super Admin may access the pre-launch data reset.', 403)
    return user, session_id


def is_armed():
    return (os.getenv('FMV_ENABLE_PRELAUNCH_RESET', '').lower() == 'true'
            and os.getenv('FMV_LAUNCH_STAGE', '').lower() == 'prelaunch')


def inventory_reversal(state):
    """Restore only existing test-order reservations never shipped or settled."""
    restore = {}
    for order in state.get('orders', {}).values():
        if order.get('status') == 'Cancelled':
            continue
        for item in order.get('items', []):
            product_id = item.get('product_id')
            quantity = item.get('quantity', 0)
            if type(quantity) is not int or quantity < 1 or product_id not in state.get('products', {}):
                raise ValueError('An order has invalid or missing catalog inventory references.')
            restore[product_id] = restore.get(product_id, 0) + quantity
    return restore


def overview(state):
    reasons = []
    if not is_armed():
        reasons.append('Server reset is not armed. Set FMV_ENABLE_PRELAUNCH_RESET=true and FMV_LAUNCH_STAGE=prelaunch for one controlled launch cleanup.')
    if state.get('prelaunch_reset_completed'):
        reasons.append('The one-time pre-launch reset has already been completed.')
    for order in state.get('orders', {}).values():
        if order.get('status') not in {'Unassigned', 'Awaiting Payment', 'Cancelled'}:
            reasons.append('A delivery has progressed beyond pre-dispatch. Complete or archive real operations separately.')
            break
        if (order.get('provider_order_id') or order.get('provider_payment_id')
                or order.get('payment_status') in {'paid', 'cod_collected'}
                or float(order.get('cod_collected') or 0) > 0):
            reasons.append('A real or external payment record exists. Financial transactions cannot be erased using Launch Reset.')
            break
    for key in ('cash_receipts', 'cash_handovers', 'driver_earnings',
                'driver_payouts', 'credit', 'return_refund_authorizations'):
        if state.get(key):
            reasons.append('Financial or payable activity exists in ' + key + '. Resolve and legally archive it before any cleanup.')
    if any(str(i.get('status', '')).lower() in {'paid', 'settled', 'refunded'} for i in state.get('invoices', {}).values()):
        reasons.append('A settled invoice exists. Preserve statutory accounting history.')
    if any(i.get('approval_status') not in {'pending', 'rejected'} for i in state.get('expenses', {}).values()):
        reasons.append('Approved driver expenses exist and must be retained for accounts.')
    try:
        restore = inventory_reversal(state)
    except (ValueError, TypeError):
        restore = {}
        reasons.append('Inventory reconciliation failed. Resolve the inconsistent test order before reset.')
    counts = {key: len(state.get(key, {})) for key in ALL_KEYS}
    retained = {key: len(state.get(key, {})) if hasattr(state.get(key, {}), '__len__') else 0 for key in KEEP_KEYS}
    return dict(enabled=is_armed(), can_reset=not reasons, blocked_reasons=list(dict.fromkeys(reasons)),
                dispatch_keys=list(DISPATCH_KEYS), accounting_keys=list(ACCOUNTING_KEYS),
                ancillary_keys=list(EXTRA_KEYS), counts=counts, preserved=retained,
                restore_stock=restore, completed=bool(state.get('prelaunch_reset_completed')),
                phrase=RESET_PHRASE, one_time=True)


class ResetExecute(BaseModel):
    model_config = ConfigDict(extra='forbid')
    ticket: str = Field(min_length=30, max_length=150)
    confirmation: str
    backup_acknowledged: bool = Field(strict=True)


def install_prelaunch_reset(app):
    c = core()

    @app.get('/admin/prelaunch-reset/preview', tags=['Super Admin: pre-launch'])
    def preview(request: Request):
        with c.read_transaction(maintain=False) as state:
            authorized(state, request)
            return overview(state)

    @app.get('/admin/prelaunch-reset/export', tags=['Super Admin: pre-launch'])
    def export(request: Request):
        with c.read_transaction(maintain=False) as state:
            user, _ = authorized(state, request)
            details = overview(state)
            c.require(details['can_reset'], 'Cannot export a reset package while the safety checks are blocked.', 409)
            # This archive contains personal data; super-admin-only HTTPS download,
            # never served as a public URL or written to public static hosting.
            package = dict(kind='fmv-prelaunch-archive',created_at=c.now(),
                           created_by=user['email'],revision=state.get('revision', 0),
                           data={key: state.get(key, {}) for key in ALL_KEYS},
                           order_emails={key: value for key, value in state.get('mail_outbox', {}).items()
                                         if str(value.get('kind', '')).startswith(('order_', 'subscription_'))},
                           inventory_reversal=details['restore_stock'])
            payload=json.dumps(package, ensure_ascii=False, allow_nan=False).encode('utf-8')
            return Response(payload, media_type='application/json',
                            headers={'Content-Disposition':'attachment; filename="fmv-prelaunch-archive.json"',
                                     'Cache-Control':'no-store', 'X-Content-Type-Options':'nosniff'})

    @app.post('/admin/prelaunch-reset/prepare', tags=['Super Admin: pre-launch'])
    def prepare(request: Request):
        with c.transaction() as state:
            user, sid = authorized(state, request)
            details = overview(state)
            c.require(details['can_reset'], 'Pre-launch reset safety checks have not passed.', 409)
            ticket = secrets.token_urlsafe(36)
            state['prelaunch_reset_ticket'] = {
                'hash': hashlib.sha256(ticket.encode()).hexdigest(),
                'user_id': user['id'], 'session_id': sid,
                'expires_at': time.time() + 300,
            }
            c.audit(state, user['email'], 'PRELAUNCH_RESET_PREPARED')
            state['prelaunch_reset_ticket']['revision'] = state['revision']
            return {'ticket':ticket,'expires_in':300,'confirmation':RESET_PHRASE,
                    'counts':details['counts'], 'stock_to_restore':details['restore_stock'],
                    'message':'Review the preview and your saved archive. No records have been deleted.'}

    @app.post('/admin/prelaunch-reset/execute', tags=['Super Admin: pre-launch'])
    def execute(request: Request, body: ResetExecute):
        with c.transaction() as state:
            user, sid = authorized(state, request)
            ticket = state.get('prelaunch_reset_ticket') or {}
            submitted_hash = hashlib.sha256(body.ticket.encode()).hexdigest()
            c.require(ticket and secrets.compare_digest(ticket.get('hash', ''), submitted_hash)
                      and ticket.get('user_id') == user['id']
                      and ticket.get('session_id') == sid
                      and ticket.get('expires_at', 0) > time.time(),
                      'Reset authorization expired. Begin again.', 403)
            c.require(ticket.get('revision') == state.get('revision', 0),
                      'Business records changed after reset preparation. Review again.', 409)
            c.require(body.confirmation == RESET_PHRASE and body.backup_acknowledged,
                      'The exact reset confirmation and archival export acknowledgement are required.', 422)
            details = overview(state)
            c.require(details['can_reset'], 'Reset is blocked by the current business safety checks.', 409)
            for product_id, quantity in details['restore_stock'].items():
                product = state['products'][product_id]
                product['stock_quantity'] += quantity
                product['version'] = product.get('version', 1) + 1
            for key in ALL_KEYS:
                state[key] = {}
            for key, item in list(state.get('mail_outbox', {}).items()):
                if str(item.get('kind', '')).startswith(('order_', 'subscription_')):
                    del state['mail_outbox'][key]
            state.pop('prelaunch_reset_ticket', None)
            state['prelaunch_reset_completed'] = dict(at=c.now(), by=user['email'],
                                                       cleared_counts=details['counts'],
                                                       restored_stock=details['restore_stock'])
            c.audit(state, user['email'], 'PRELAUNCH_TEST_DISPATCH_AND_ACCOUNTS_RESET')
            return {'status':'reset_completed', 'message':'Pre-launch test dispatch and accounting records cleared in one transaction.',
                    'cleared_counts':details['counts'],
                    'restored_stock':details['restore_stock'],
                    'protected_records_retained':list(KEEP_KEYS)}
