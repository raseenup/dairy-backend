"""Cross-portal regressions; providers and production credentials are never used."""
import base64
import io
import json
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from main import app
import experience as exp
import operations as ops
import secure_backend as sec
from mail_routing import sender_for
from test_secure_flows import account, checkout, photo, signup, verify

client=TestClient(app)


def delivered_cash():
    customer,_=account()
    admin,_=account('admin@example.com','admin','9876543211')
    driver,user=account('driver@example.com','driver','9876543212')
    order=checkout(customer).json()['order']
    assert client.patch('/admin/deliveries/'+order['id']+'/assign',headers=admin,json={'driver_key':user['id']}).status_code==200
    assert client.post('/driver/shifts/clock-in',headers=driver,json={'vehicle_plate':'KL07AB1234','odometer_start':100,
        'checklist_json':{'tires':True,'brakes':True,'fuel':True,'cold_chain':True}}).status_code==200
    assert client.patch('/driver/deliveries/'+order['id']+'/epod',headers=driver,json={'status':'Delivered',
        'delivery_otp':order['delivery_otp'],'epod_photo_url':photo(),'cod_collected':order['cod_amount']}).status_code==200
    return customer,admin,driver,user,order


def receipt(admin,driver_id,amount=50,key='cash-receipt-idempotency-1234',**values):
    return client.post('/admin/finance/cash-receipts',headers={**admin,'Idempotency-Key':key},
        json=dict(driver_id=driver_id,amount=amount,reference='Accounts voucher 001',confirmed_received=True,**values))


def test_cancel_mail_commits_once_with_stock_and_correct_purpose():
    customer,_=account();order=checkout(customer).json()['order']
    for _ in range(2):assert client.post('/orders/'+order['id']+'/cancel',headers=customer).status_code==200
    with sec.read_transaction() as state:
        messages=[m for m in state['mail_outbox'].values() if m['kind']=='order_cancelled']
        assert len(messages)==1
        assert messages[0]['to']=='buyer@example.com'
        assert 'Purpose: Order cancelled' in messages[0]['content']['text']
        assert 'FROM FIRST MILK VENTURE' in messages[0]['content']['text']
        assert state['products']['prod-4']['stock_quantity']==250
        assert state['orders'][order['id']]['payment_status']=='cancelled'


def test_driver_signup_banner_response_and_approval_mail_once():
    admin,_=account('admin@example.com','admin')
    pending=verify(signup('driver@example.com','driver','9876543211'))
    assert pending.json()['pending_approval'] and 'Application submitted successfully' in pending.json()['message']
    assert 'access_token' not in pending.json()
    with sec.read_transaction() as state:driver=state['users']['driver:driver@example.com']
    for _ in range(2):assert client.post('/admin/drivers/'+driver['id']+'/approve',headers=admin).status_code==200
    with sec.read_transaction() as state:
        messages=[m for m in state['mail_outbox'].values() if m['kind']=='driver_approval']
        assert len(messages)==1 and messages[0]['to']=='driver@example.com'
        notifications=[n for n in state['notifications'].values() if n.get('portal')=='driver']
        assert len(notifications)==1 and 'approved successfully' in notifications[0]['message']


@pytest.mark.parametrize('kind,address',[
    ('signup_verification','auth@'),('signin_verification','auth@'),('password_reset','auth@'),
    ('order_placed','orders@'),('order_cancelled','orders@'),('order_shipped','delivery@'),
    ('order_out_for_delivery','delivery@'),('order_delivered','delivery@'),
    ('driver_approval','drivers@'),('admin_invitation','admin@'),('cash_receipt','accounts@'),
    ('support_update','support@'),('offer','offers@'),('security_notice','security@')])
def test_department_sender_mapping(monkeypatch,kind,address):
    for variable,_ in __import__('mail_routing').SENDERS.values():monkeypatch.delenv(variable,raising=False)
    monkeypatch.setenv('ORDER_EMAIL_SENDER','Legacy sender <auth@firstmilkventure.com>')
    assert address in sender_for(kind)


def test_configured_sender_is_used_by_mail_provider(monkeypatch):
    calls=[]
    monkeypatch.setenv('RESEND_API_KEY','isolated-fake-key')
    monkeypatch.setenv('DRIVER_EMAIL_SENDER','Fleet Team <fleet@firstmilkventure.com>')
    monkeypatch.setattr(exp.httpx,'post',lambda *args,**kwargs:(calls.append(kwargs['json']) or httpx.Response(200,json={'id':'fake'})))
    exp.send_rendered_mail({'id':'test-mail','kind':'driver_approval','to':'driver@example.com','content':{'subject':'Approved','html':'Approved','text':'Approved'}})
    assert calls[0]['from']=='Fleet Team <fleet@firstmilkventure.com>'


def test_cash_partial_receipts_idempotency_and_carry_across_dates():
    _,admin,driver,user,order=delivered_cash()
    with sec.transaction() as state:state['orders'][order['id']]['delivered_time']='2026-01-01T10:00:00+05:30'
    cash=client.get('/driver/reconciliation',headers=driver).json()
    assert cash['total_cod_collected']==0 and cash['net_cash_to_remit']==120
    first=receipt(admin,user['id'])
    assert first.status_code==200,first.text
    assert first.json()['cash']['net_cash_to_remit']==70
    assert receipt(admin,user['id']).json()['receipt']['id']==first.json()['receipt']['id']
    assert receipt(admin,user['id'],amount=51).status_code==409
    assert receipt(admin,user['id'],amount=71,key='another-receipt-overpayment-123').status_code==409
    assert receipt(admin,user['id'],amount=70,key='another-receipt-exact-123456').status_code==200
    assert client.get('/driver/reconciliation',headers=driver).json()['net_cash_to_remit']==0
    with sec.read_transaction() as state:assert len(state['cash_receipts'])==2


def test_driver_submission_does_not_clear_cash_and_finance_confirmation_does():
    _,admin,driver,user,_=delivered_cash()
    response=client.post('/driver/cash/handovers',headers={**driver,'Idempotency-Key':'driver-handover-key-123456'},json={'amount':120,'reference':'Cash handed to cashier'})
    assert response.status_code==200,response.text
    handover=response.json()['handover']
    cash=client.get('/driver/reconciliation',headers=driver).json()
    assert cash['net_cash_to_remit']==120 and cash['pending_handover']==120 and cash['available_to_submit']==0
    assert receipt(driver,user['id'],120,handover_id=handover['id']).status_code==403
    assert receipt(admin,user['id'],120,handover_id=handover['id']).status_code==200
    assert receipt(admin,user['id'],120,key='different-confirmation-key-1234',handover_id=handover['id']).status_code==409
    cash=client.get('/driver/reconciliation',headers=driver).json()
    assert cash['net_cash_to_remit']==0 and cash['handovers'][0]['status']=='received'


def test_pending_expenses_need_accounts_review_and_cannot_self_clear_cash():
    _,admin,driver,user,_=delivered_cash()
    response=client.post('/driver/expenses',headers=driver,json={'category':'Fuel','amount':20})
    assert response.status_code==200,response.text
    expense=response.json()['expense']
    cash=client.get('/driver/reconciliation',headers=driver).json()
    assert cash['net_cash_to_remit']==120 and cash['pending_expenses']==20
    url='/admin/finance/expenses/'+expense['id']
    assert client.patch(url,headers=driver,json={'status':'approved'}).status_code==403
    assert client.patch(url,headers=admin,json={'status':'approved'}).status_code==200
    assert client.patch(url,headers=admin,json={'status':'approved'}).status_code==409
    cash=client.get('/driver/reconciliation',headers=driver).json()
    assert cash['net_cash_to_remit']==100 and cash['approved_expenses']==20


def test_concurrent_receipts_never_overclear_cash():
    _,admin,_,user,_=delivered_cash()
    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses=list(pool.map(lambda n:receipt(admin,user['id'],100,key='concurrent-cash-receipt-key-'+str(n)).status_code,[1,2]))
    assert sorted(statuses)==[200,409]
    with sec.read_transaction() as state:
        assert ops.driver_cash(state,user['id'])['net_cash_to_remit']==20


def test_cash_requires_finance_role_and_explicit_receipt_confirmation():
    _,admin,driver,user,_=delivered_cash()
    operations,_=account('ops@example.com','admin','9876543213',role='operations_manager')
    assert receipt(operations,user['id']).status_code==403
    response=client.post('/admin/finance/cash-receipts',headers={**admin,'Idempotency-Key':'unconfirmed-cash-receipt-1234'},json={'driver_id':user['id'],'amount':50,'reference':'R01','confirmed_received':False})
    assert response.status_code==422
    assert client.get('/admin/finance/overview',headers=driver).status_code==403


def test_snapshots_are_scoped_and_do_not_expose_pin_to_driver():
    customer,admin,driver,_,order=delivered_cash()
    other,_=account('other@example.com',phone='9876543213')
    assert client.get('/portal/snapshot',headers=other).json()['orders']==[]
    snapshot=client.get('/portal/snapshot',headers=driver).json()
    assert 'delivery_otp' not in snapshot['orders'][0] and 'otp_hash' not in snapshot['orders'][0]
    assert snapshot['cash']['net_cash_to_remit']==120
    snapshot=client.get('/portal/snapshot',headers=customer).json()
    assert snapshot['orders'][0]['delivery_otp']==order['delivery_otp'] and 'finance' not in snapshot
    with sec.transaction() as state:sec.notify(state,'buyer@example.com','Driver-only update','Private driver notice',portal='driver')
    assert all(n['title']!='Driver-only update' for n in client.get('/portal/notifications',headers=customer).json()['notifications'])
    client.post('/portal/notifications/mark-read',headers=customer)
    with sec.read_transaction() as state:assert next(n for n in state['notifications'].values() if n['title']=='Driver-only update')['read'] is False
    summary=client.get('/admin/order-summary',headers=admin).json()
    assert summary['completed']==1 and summary['unassigned']==0
    finance,_=account('finance@example.com','admin','9876543214',role='finance')
    snapshot=client.get('/portal/snapshot',headers=finance).json()
    assert 'finance' in snapshot and 'orders' not in snapshot and 'products' not in snapshot
    assert client.get('/admin/order-summary',headers=finance).status_code==403


def test_reads_use_snapshots_without_state_writes_and_sync_checks_revocation(monkeypatch):
    headers,_=account()
    with sec.read_transaction() as state:before=state['revision']
    original=sec.transaction
    def forbid_write(*args,**kwargs):raise AssertionError('Read attempted to lock or rewrite state')
    monkeypatch.setattr(sec,'transaction',forbid_write)
    assert client.get('/products',headers=headers).status_code==200
    assert client.get('/portal/snapshot',headers=headers).status_code==200
    assert client.get('/sync',headers=headers).json()['revision']==before
    monkeypatch.setattr(sec,'transaction',original)
    assert client.post('/auth/logout',headers=headers).status_code==200
    assert client.get('/sync',headers=headers).status_code==401


def test_slow_otp_mail_does_not_lock_reads_or_checkout(monkeypatch):
    headers,_=account();started=threading.Event();release=threading.Event()
    def slow_send(*args,**kwargs):
        started.set()
        assert release.wait(5)
    monkeypatch.setattr(sec,'send_code',slow_send)
    with ThreadPoolExecutor(max_workers=2) as pool:
        future=pool.submit(signup,'new@example.com','customer','9876543211')
        assert started.wait(2)
        try:
            assert pool.submit(lambda:client.get('/products',headers=headers)).result(timeout=2).status_code==200
            assert pool.submit(checkout,headers).result(timeout=2).status_code==200
        finally:release.set()
        assert future.result(timeout=2).status_code==200


def test_catalog_text_and_animated_gif_edit_with_conflict_control():
    admin,_=account('admin@example.com','admin');customer,_=account(phone='9876543211')
    out=io.BytesIO();frames=[Image.new('RGB',(30,30),color) for color in ['red','green']]
    frames[0].save(out,'GIF',save_all=True,append_images=frames[1:],duration=200,loop=0)
    payload={'filename':'product.gif','data_base64':base64.b64encode(out.getvalue()).decode()}
    assert client.post('/admin/product-media',headers=customer,json=payload).status_code==403
    response=client.post('/admin/product-media',headers=admin,json=payload)
    assert response.status_code==200,response.text
    asset=response.json();public=client.get(asset['url'])
    assert public.content==out.getvalue() and public.headers['content-type']=='image/gif'
    assert 'immutable' in public.headers['cache-control']
    body=dict(version=1,name='Updated milk',description='Product details',unit='1 L',price=65,
              stock_quantity=200,min_stock_threshold=10,tax_rate=0,media_id=asset['id'])
    url='/admin/products/prod-4'
    assert client.patch(url,headers=customer,json=body).status_code==403
    assert client.patch(url,headers=admin,json=body).status_code==200
    assert client.patch(url,headers=admin,json=body).status_code==409
    product=next(p for p in client.get('/products',headers=customer).json() if p['id']=='prod-4')
    assert product['name']=='Updated milk' and product['image_url']==asset['url']
    body.update(version=2,media_id='')
    assert client.patch(url,headers=admin,json=body).json()['product']['image_url']==''
    bad={'filename':'bad.svg','data_base64':base64.b64encode(b'<svg onload="alert(1)"/>').decode()}
    assert client.post('/admin/product-media',headers=admin,json=bad).status_code==422


def test_media_bytes_are_separate_from_polled_state_and_create_product_uses_upload():
    admin,_=account('admin@example.com','admin')
    out=io.BytesIO();Image.new('RGB',(30,30),'green').save(out,'PNG')
    asset=client.post('/admin/product-media',headers=admin,json={'filename':'p.png','data_base64':base64.b64encode(out.getvalue()).decode()}).json()
    response=client.post('/admin/products',headers=admin,json={'name':'Paneer','description':'Fresh paneer',
        'unit':'200g','price':50,'stock_quantity':10,'min_stock_threshold':2,'image_url':asset['url']})
    assert response.status_code==200,response.text
    with sec.read_transaction() as state:assert 'data_base64' not in json.dumps(state['products'])
