"""Launch Reset safety contract (in-memory, no destructive database calls)."""
from copy import deepcopy

import pytest

from prelaunch_reset import inventory_reversal, overview


def demo():
    return {
        'products': {'milk': {'id': 'milk', 'stock_quantity': 5, 'version': 1}},
        'orders': {'ORDER1': {
            'id': 'ORDER1', 'status': 'Unassigned', 'payment_status': 'cod_due',
            'items': [{'product_id': 'milk', 'quantity': 2}],
        }},
        'invoices': {'INV1': {'status': 'unpaid'}},
        'users': {'driver:example.test': {'portal': 'driver'}},
        'drivers': {'D1': {'approved': True}},
        'audit': [],
    }


@pytest.fixture
def armed(monkeypatch):
    monkeypatch.setenv('FMV_ENABLE_PRELAUNCH_RESET', 'true')
    monkeypatch.setenv('FMV_LAUNCH_STAGE', 'prelaunch')


def test_preview_is_read_only_and_restores_test_order_reservations(armed):
    data = demo()
    before = deepcopy(data)
    result = overview(data)
    assert result['can_reset'] is True
    assert result['restore_stock'] == {'milk': 2}
    assert result['counts']['orders'] == 1
    assert result['counts']['invoices'] == 1
    assert data == before


def test_reset_is_off_by_default(monkeypatch):
    monkeypatch.delenv('FMV_ENABLE_PRELAUNCH_RESET', raising=False)
    monkeypatch.delenv('FMV_LAUNCH_STAGE', raising=False)
    assert not overview(demo())['can_reset']


@pytest.mark.parametrize('key', [
    'cash_receipts', 'cash_handovers', 'driver_payouts',
    'driver_earnings', 'return_refund_authorizations'
])
def test_financial_obligations_block_reset(armed, key):
    data = demo()
    data[key] = {'record1': {'status': 'pending'}}
    result = overview(data)
    assert result['can_reset'] is False
    assert result['blocked_reasons']


@pytest.mark.parametrize('status', ['Delivered', 'Assigned', 'In Transit'])
def test_progressed_deliveries_block_reset(armed, status):
    data = demo()
    data['orders']['ORDER1']['status'] = status
    if status == 'Assigned':
        # Dispatch can reassign a previously started order back to Assigned.
        data['audit'].append({'event':'DELIVERY_IN TRANSIT','object_id':'ORDER1'})
    assert not overview(data)['can_reset']


@pytest.mark.parametrize('payment_field,payment_value', [
    ('payment_status', 'paid'), ('provider_order_id', 'order_provider_1'),
    ('provider_payment_id', 'payment_provider_1'), ('cod_collected', 55),
])
def test_real_or_external_payments_block_reset(armed, payment_field, payment_value):
    data = demo()
    data['orders']['ORDER1'][payment_field] = payment_value
    assert not overview(data)['can_reset']


def test_one_time_only(armed):
    data = demo()
    data['prelaunch_reset_completed'] = {'at': '2026-01-01T00:00:00+05:30'}
    assert not overview(data)['can_reset']


def test_inventory_is_not_reversed_for_cancelled_orders():
    data = demo()
    data['orders']['ORDER1']['status'] = 'Cancelled'
    assert inventory_reversal(data) == {}


def test_invalid_stock_reference_blocks_reset(armed):
    data = demo()
    data['orders']['ORDER1']['items'][0]['product_id'] = 'removed-product'
    result = overview(data)
    assert result['can_reset'] is False
    assert 'inventory' in ' '.join(result['blocked_reasons']).lower()


def test_active_shift_blocks_reset(armed):
    data = demo()
    data['shifts'] = {'SHIFT1': {'status': 'active'}}
    assert not overview(data)['can_reset']


def test_unstarted_assigned_test_order_can_be_cleared(armed):
    data = demo()
    data['orders']['ORDER1']['status'] = 'Assigned'
    assert overview(data)['can_reset']


def test_only_operation_notifications_are_selected(armed):
    from prelaunch_reset import reset_notifications
    data = demo()
    data['orders'] = {'ORDER10001': data['orders']['ORDER1']}
    data['notifications'] = {
        'N1': {'title': 'Order assigned', 'message': 'Order ORDER10001'},
        'N2': {'title': 'Your password', 'message': 'Security notification'},
    }
    assert list(reset_notifications(data)) == ['N1']
    assert overview(data)['counts']['operation_notifications'] == 1


def test_reset_endpoints_are_super_admin_only_and_one_time(armed, monkeypatch):
    """Exercise actual registered routes with isolated, disposable state."""
    from contextlib import contextmanager
    from types import SimpleNamespace
    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient
    import prelaunch_reset as reset

    state = demo()
    state['revision'] = 0
    state['mail_outbox'] = {
        'mail-order': {'kind': 'order_placed', 'status': 'queued'},
        'mail-auth': {'kind': 'signup_verification', 'status': 'queued'},
    }
    state['notifications'] = {
        'auth': {'title': 'Welcome', 'message': 'Account verified.'},
        'order': {'title': 'Assigned order', 'message': 'ORDER00001'},
    }
    original_order = state['orders'].pop('ORDER1')
    state['orders']['ORDER00001'] = original_order
    actor_info = {'id': 'super-1', 'email': 'owner@example.test',
                  'portal': 'admin', 'role': 'super_admin'}

    @contextmanager
    def fake_read_transaction(maintain=False):
        yield state

    @contextmanager
    def fake_transaction():
        yield state

    def fake_require(ok, message='', status=400):
        if not ok:
            raise HTTPException(status, message)

    def fake_actor(db, request, role):
        assert role == 'admin'
        return actor_info, 'session-1'

    def fake_audit(db, actor_name, event, object_id=''):
        db['revision'] += 1
        db.setdefault('audit', []).append({'actor': actor_name, 'event': event})

    proxy = SimpleNamespace(
        actor=fake_actor, require=fake_require, audit=fake_audit,
        transaction=fake_transaction, read_transaction=fake_read_transaction,
        now=lambda: '2026-11-22T10:00:00+05:30',
    )
    monkeypatch.setattr(reset, 'core', lambda: proxy)
    app = FastAPI()
    reset.install_prelaunch_reset(app)
    client = TestClient(app)

    actor_info['role'] = 'finance'
    assert client.get('/admin/prelaunch-reset/preview').status_code == 403
    assert not state.get('prelaunch_reset_completed')

    actor_info['role'] = 'super_admin'
    assert client.get('/admin/prelaunch-reset/preview').json()['can_reset']
    downloaded = client.get('/admin/prelaunch-reset/export')
    assert downloaded.status_code == 200
    archive = downloaded.json()
    assert 'ORDER00001' in archive['data']['orders']
    assert archive['operation_notifications'] == {'order': state['notifications']['order']}

    prepared = client.post('/admin/prelaunch-reset/prepare').json()
    assert prepared['ticket']
    payload = {
        'ticket': prepared['ticket'],
        'confirmation': prepared['confirmation'],
        'backup_acknowledged': True,
        'inventory_acknowledged': True,
    }
    result = client.post('/admin/prelaunch-reset/execute', json=payload)
    assert result.status_code == 200, result.text
    assert result.json()['status'] == 'reset_completed'
    assert not state['orders'] and not state['invoices']
    assert state['products']['milk']['stock_quantity'] == 7
    assert state['users'] and state['drivers']
    assert state['mail_outbox'] == {'mail-auth': {'kind': 'signup_verification', 'status': 'queued'}}
    assert state['notifications'] == {'auth': {'title': 'Welcome', 'message': 'Account verified.'}}
    assert state['prelaunch_reset_completed']
    assert client.post('/admin/prelaunch-reset/prepare').status_code == 409
    assert client.post('/admin/prelaunch-reset/execute', json=payload).status_code == 403


def test_reset_rejects_a_state_change_after_prepare(armed, monkeypatch):
    from contextlib import contextmanager
    from types import SimpleNamespace
    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient
    import prelaunch_reset as reset

    state = demo()
    state['revision'] = 0

    @contextmanager
    def txn(*args, **kwargs):
        yield state

    def require(ok, message='', status=400):
        if not ok:
            raise HTTPException(status, message)

    def audit(db, user, event, id=''):
        db['revision'] += 1

    proxy = SimpleNamespace(
        transaction=txn, read_transaction=txn, require=require,
        actor=lambda db, request, portal: ({'id': 'super','email':'owner@example.test',
                                            'role':'super_admin'}, 's1'),
        audit=audit, now=lambda:'2026-11-22T00:00:00+05:30',
    )
    monkeypatch.setattr(reset, 'core', lambda: proxy)
    app = FastAPI()
    reset.install_prelaunch_reset(app)
    client = TestClient(app)
    prepared = client.post('/admin/prelaunch-reset/prepare').json()
    state['revision'] += 1  # Simulate another authenticated operation.
    response = client.post('/admin/prelaunch-reset/execute', json={
        'ticket': prepared['ticket'],
        'confirmation': prepared['confirmation'],
        'backup_acknowledged': True,
        'inventory_acknowledged': True,
    })
    assert response.status_code == 409
    assert state['orders'] and not state.get('prelaunch_reset_completed')


@pytest.mark.parametrize('evidence', ['fulfillment_started_at','delivery_attempts','overtime_review'])
def test_reset_preserves_operations_with_history(armed, evidence):
    state = demo()
    state['orders']['ORDER1']['status'] = 'Assigned'
    if evidence == 'fulfillment_started_at':
        state['orders']['ORDER1'][evidence] = '2026-01-01T10:00:00+05:30'
    elif evidence == 'delivery_attempts':
        state[evidence] = {'ATTEMPT1':{'order_id':'ORDER1'}}
    else:
        state['shifts'] = {'SHIFT1':{'status':'completed','overtime_review':{'status':'pending'}}}
    assert not overview(state)['can_reset']
