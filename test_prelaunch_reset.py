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
