import base64
import hashlib
import hmac
import io
import json
import time
from concurrent.futures import ThreadPoolExecutor
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from main import app
import secure_backend as sec

client=TestClient(app)
PASSWORD='CorrectAccountPassword123!'

def signup(email='buyer@example.com',portal='customer',phone='9876543210',invitation=''):
    return client.post('/auth/signup',json=dict(email=email,phone=phone,password=PASSWORD,full_name='Kerala Buyer',address='MG Road, Kochi, Kerala 682001',portal=portal,invitation=invitation))

def otp_for(cid):
    with sec.transaction() as s:
        ch=s['challenges'][cid]
        ch['code_hash']=hmac.new(sec.SECRET.encode(),(cid+'123456').encode(),'sha256').hexdigest()
    return '123456'

def verify(response):
    assert response.status_code==200,response.text
    cid=response.json()['challenge_id'];otp_for(cid)
    return client.post('/auth/verify-otp',json={'challenge_id':cid,'otp':'123456'})

def account(email='buyer@example.com',portal='customer',phone='9876543210',role='super_admin'):
    invite=''
    if portal=='admin':
        invite='one-time-staff-invitation-'+email
        with sec.transaction() as s:s['invites'][hashlib.sha256(invite.encode()).hexdigest()]={'email':email,'role':role,'expires':time.time()+300,'used':False}
    response=verify(signup(email,portal,phone,invite))
    if portal=='driver':
        assert response.json()['pending_approval']
        with sec.transaction() as s:
            u=s['users']['driver:'+email];u['approved']=True;s['drivers'][u['id']]['approved']=True
        response=verify(client.post('/auth/signin',json={'identifier':email,'password':PASSWORD,'portal':'driver'}))
    assert response.status_code==200,response.text
    return {'Authorization':'Bearer '+response.json()['access_token']},response.json()['user']

def checkout(headers,items=None,key='checkout-key-123456789',**extra):
    return client.post('/orders/checkout',headers={**headers,'Idempotency-Key':key},json=dict(items=items or [{'product_id':'prod-4','quantity':2}],payment_method='COD',**extra))

def photo():
    out=io.BytesIO();Image.new('RGB',(40,40),'green').save(out,'PNG')
    return 'data:image/png;base64,'+base64.b64encode(out.getvalue()).decode()

def test_auth_requires_otp_and_hashes_password():
    response=signup();assert 'access_token' not in response.json()
    with sec.transaction() as s:assert not s['users']
    response=verify(response);assert response.json()['access_token']
    with sec.transaction() as s:
        u=s['users']['customer:buyer@example.com'];assert 'password' not in u;assert u['password_hash'].startswith('scrypt-v2$')
    signin=client.post('/auth/signin',json={'identifier':'9876543210','password':PASSWORD})
    assert signin.status_code==200 and 'access_token' not in signin.json()


def test_missing_expired_wrong_and_replayed_otp():
    assert client.post('/auth/verify-otp',json={'challenge_id':'missing-challenge','otp':'123456'}).status_code==400
    response=signup();cid=response.json()['challenge_id'];otp_for(cid)
    for n in range(5):assert client.post('/auth/verify-otp',json={'challenge_id':cid,'otp':'654321'}).status_code==400
    assert client.post('/auth/verify-otp',json={'challenge_id':cid,'otp':'123456'}).status_code==400
    with sec.transaction() as s:assert s['challenges'][cid]['attempts']==5
    response=signup(email='other@example.com',phone='9876543211');cid=response.json()['challenge_id'];otp_for(cid)
    assert client.post('/auth/verify-otp',json={'challenge_id':cid,'otp':'123456'}).status_code==200
    assert client.post('/auth/verify-otp',json={'challenge_id':cid,'otp':'123456'}).status_code==400
    response=signup(email='third@example.com',phone='9876543212');cid=response.json()['challenge_id'];otp_for(cid)
    with sec.transaction() as s:s['challenges'][cid]['expires']=time.time()-1
    assert client.post('/auth/verify-otp',json={'challenge_id':cid,'otp':'123456'}).status_code==400


def test_checkout_authority_idempotency_and_stock():
    headers,u=account()
    response=checkout(headers,items=[{'product_id':'prod-4','quantity':2,'price':0.01,'name':'forged name'}],customer_email='victim@example.com',total_amount=0.02)
    assert response.status_code==200,response.text
    o=response.json()['order'];assert o['total_amount']==120;assert o['owner']==u['email']
    again=checkout(headers,items=[{'product_id':'prod-4','quantity':2,'price':0.01,'name':'forged name'}],customer_email='victim@example.com',total_amount=0.02)
    assert again.json()['order']['id']==o['id']
    products=client.get('/products',headers=headers).json();assert next(p for p in products if p['id']=='prod-4')['stock_quantity']==248
    assert checkout(headers,items=[{'product_id':'prod-4','quantity':3}]).status_code==409
    assert client.post('/orders/checkout',json={'items':[{'product_id':'prod-4','quantity':1}]}).status_code==401

@pytest.mark.parametrize('lines',[
    [{'product_id':'prod-4','quantity':-1}],
    [{'product_id':'prod-4','quantity':0}],
    [{'product_id':'prod-4','quantity':1.5}],
    [{'product_id':'prod-4','quantity':True}],
    [{'product_id':'prod-4','quantity':200},{'product_id':'prod-4','quantity':100}],
    [{'product_id':'prod-4','quantity':1},{'product_id':'missing','quantity':1}],
])
def test_failed_checkout_never_partially_deducts(lines):
    h,_=account();response=checkout(h,lines);assert response.status_code in {404,409,422}
    with sec.transaction() as s:assert s['products']['prod-4']['stock_quantity']==250;assert not s['orders']


def test_concurrent_last_stock_only_one_order():
    h,_=account()
    with sec.transaction() as s:s['products']['prod-4']['stock_quantity']=1
    def buy(n):return checkout(h,[{'product_id':'prod-4','quantity':1}],key='concurrent-order-key-'+str(n)).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(buy,[1,2]))
    assert sorted(results)==[200,409]
    with sec.transaction() as s:assert s['products']['prod-4']['stock_quantity']==0;assert len(s['orders'])==1


def test_stock_price_edit_conflict_and_reversal():
    customer,_=account();admin,_=account('admin@example.com','admin','9876543211')
    url='/admin/products/prod-4/stock'
    assert client.patch(url,headers=customer,json={'stock_quantity':5,'price':70}).status_code==403
    assert client.patch(url,headers=admin,json={'stock_quantity':5,'price':70,'version':1}).status_code==200
    assert client.patch(url,headers=admin,json={'stock_quantity':8,'version':1}).status_code==409
    order=checkout(customer).json()['order'];assert order['total_amount']==140
    for _ in range(2):assert client.post('/orders/'+order['id']+'/cancel',headers=customer).status_code==200
    with sec.transaction() as s:assert s['products']['prod-4']['stock_quantity']==5


def test_individual_roles_and_public_admin_signup_blocked():
    assert signup('admin@example.com','admin','9876543210').status_code==403
    assert client.post('/admin/rbac/verify',json={'role':'super_admin','access_key':'Firstmilk3211234'}).status_code==410
    assert client.get('/admin/drivers',headers={'x-admin-key':'Firstmilk3211234'}).status_code==403
    h,_=account('finance@example.com','admin',role='finance')
    assert client.get('/admin/finance/overview',headers=h).status_code==200
    assert client.get('/admin/drivers',headers=h).status_code==403


def test_cross_customer_data_isolation():
    a,_=account();b,_=account('second@example.com',phone='9876543211')
    order=checkout(a).json()['order']
    assert client.get('/orders',headers=b).json()==[]
    assert client.post('/orders/'+order['id']+'/cancel',headers=b).status_code==404
    with sec.transaction() as s:invoice=next(iter(s['invoices'].values()))['id']
    assert client.get('/portal/billing/invoices/'+invoice+'/download',headers=b).status_code==404
    assert client.get('/portal/team').status_code==401


def test_driver_assignment_epod_cod_and_shift_isolation():
    customer,_=account();admin,_=account('admin@example.com','admin','9876543211');driver,du=account('driver@example.com','driver','9876543212');other,_=account('otherdriver@example.com','driver','9876543213')
    order=checkout(customer).json()['order'];oid=order['id']
    assert client.get('/driver/deliveries',headers=driver).json()==[]
    assert client.patch('/admin/deliveries/'+oid+'/assign',headers=admin,json={'driver_key':du['id']}).status_code==200
    rows=client.get('/driver/deliveries',headers=driver).json();assert rows[0]['id']==oid;assert 'delivery_otp' not in rows[0];assert 'otp_hash' not in rows[0]
    assert client.patch('/driver/deliveries/'+oid+'/epod',headers=other,json={'status':'Delivered','delivery_otp':order['delivery_otp']}).status_code==404
    sh=client.post('/driver/shifts/clock-in',headers=driver,json={'vehicle_plate':'KL07AB1234','odometer_start':100,'checklist_json':{'tires':True,'brakes':True,'fuel':True,'cold_chain':True}}).json()['shift']
    assert client.patch('/driver/shifts/'+sh['id'],headers=other,json={'status':'completed','odometer_end':105}).status_code==404
    body={'status':'Delivered','delivery_otp':order['delivery_otp'],'epod_photo_url':photo(),'cod_collected':order['cod_amount']}
    assert client.patch('/driver/deliveries/'+oid,headers=driver,json={'status':'Delivered'}).status_code==400
    wrong=dict(body,delivery_otp='0000');assert client.patch('/driver/deliveries/'+oid+'/epod',headers=driver,json=wrong).status_code==400
    assert client.patch('/driver/deliveries/'+oid+'/epod',headers=driver,json=dict(body,cod_collected=0)).status_code==400
    assert client.patch('/driver/deliveries/'+oid+'/epod',headers=driver,json=dict(body,epod_photo_url='photo.jpg')).status_code==422
    assert client.patch('/driver/deliveries/'+oid+'/epod',headers=driver,json=body).status_code==200
    assert client.patch('/driver/deliveries/'+oid+'/epod',headers=driver,json=body).status_code==409
    assert client.get('/driver/reconciliation',headers=driver).json()['total_cod_collected']==120
    assert client.get('/orders',headers=customer).json()[0]['status']=='Delivered'


def test_break_clockout_and_subscription_dispatch():
    customer,_=account();admin,_=account('admin@example.com','admin','9876543211');driver,_=account('driver@example.com','driver','9876543212')
    body={'product_id':'prod-4','quantity':2,'frequency':'Daily','start_date':sec.today()}
    assert client.post('/subscriptions',headers=customer,json=body).status_code==200
    for _ in range(2):assert client.post('/admin/generate-deliveries',headers=admin,json={'target_date':sec.today()}).status_code==200
    with sec.transaction() as s:assert len(s['orders'])==1;assert s['products']['prod-4']['stock_quantity']==248
    sh=client.post('/driver/shifts/clock-in',headers=driver,json={'vehicle_plate':'KL07AB1234','odometer_start':100,'checklist_json':{'tires':True,'brakes':True,'fuel':True,'cold_chain':True}}).json()['shift']
    assert client.patch('/driver/shifts/'+sh['id'],headers=driver,json={'status':'on_break'}).status_code==200
    assert client.patch('/driver/shifts/'+sh['id'],headers=driver,json={'status':'active'}).status_code==200
    assert client.patch('/driver/shifts/'+sh['id'],headers=driver,json={'status':'completed','odometer_end':110}).status_code==200
    assert client.get('/driver/shifts/active',headers=driver).json()['shift'] is None
    assert 'work_seconds' in client.get('/admin/attendance',headers=admin).json()['shifts'][0]


def test_provider_payment_signature_capture_and_replay(monkeypatch):
    monkeypatch.setenv('RAZORPAY_KEY_ID','rzp_test_example');monkeypatch.setenv('RAZORPAY_KEY_SECRET','provider-secret');monkeypatch.setenv('RAZORPAY_WEBHOOK_SECRET','webhook-secret')
    customer,_=account();order=checkout(customer,payment_method='Online').json()['order'] if False else client.post('/orders/checkout',headers={**customer,'Idempotency-Key':'online-order-key-123456'},json={'items':[{'product_id':'prod-4','quantity':2}],'payment_method':'Online'}).json()['order']
    monkeypatch.setattr(sec,'razorpay_call',lambda method,path,data=None:{'id':'order_provider'} if path=='orders' else {'id':'pay_provider','order_id':'order_provider','amount':12000,'currency':'INR','status':'captured'})
    assert client.post('/payments/create',headers=customer,json={'order_id':order['id']}).status_code==200
    body={'order_id':order['id'],'razorpay_order_id':'order_provider','razorpay_payment_id':'pay_provider','razorpay_signature':'forged'}
    assert client.post('/payments/verify',headers=customer,json=body).status_code==400
    body['razorpay_signature']=hmac.new(b'provider-secret',b'order_provider|pay_provider','sha256').hexdigest()
    assert client.post('/payments/verify',headers=customer,json=body).json()['order']['payment_status']=='paid'
    raw=json.dumps({'event':'payment.captured','payload':{'payment':{'entity':{'id':'pay_provider','order_id':'order_provider'}}}}).encode()
    h={'x-razorpay-signature':hmac.new(b'webhook-secret',raw,'sha256').hexdigest(),'x-razorpay-event-id':'evt_example','Content-Type':'application/json'}
    assert client.post('/webhooks/payment',content=raw,headers=h).status_code==200
    assert client.post('/webhooks/payment',content=raw,headers=h).json()['duplicate'] is True
    assert client.post('/webhooks/payment',json={'event':'payment.succeeded'}).status_code==401


def test_email_failure_never_creates_account(monkeypatch):
    def fail(*args):raise sec.HTTPException(503,'Email unavailable')
    monkeypatch.setattr(sec,'send_code',fail)
    assert signup().status_code==503
    with sec.transaction() as s:assert not s['users'] and not s['challenges']


def test_logout_reset_invalidate_old_tokens():
    h,u=account();assert client.get('/auth/session',headers=h).status_code==200
    assert client.post('/auth/logout',headers=h).status_code==200
    assert client.get('/auth/session',headers=h).status_code==401
    signed=verify(client.post('/auth/signin',json={'identifier':u['email'],'password':PASSWORD}));old={'Authorization':'Bearer '+signed.json()['access_token']}
    reset=client.post('/auth/password-reset/request',json={'email':u['email'],'portal':'customer'});result=verify(reset)
    assert client.post('/auth/password-reset/confirm',json={'reset_token':result.json()['reset_token'],'new_password':'ChangedPassword123!'}).status_code==200
    assert client.get('/auth/session',headers=old).status_code==401


def test_static_allowlist_and_security_headers(tmp_path, monkeypatch):
    from fastapi import FastAPI
    # Explicit temporary assets model optional integration hosting without
    # depending on any other repository or exposing arbitrary backend files.
    backend=tmp_path/'Backend';backend.mkdir()
    portal=tmp_path/'Customer Portal';portal.mkdir()
    (portal/'index.html').write_text('<!DOCTYPE html><html><body>Test portal</body></html>')
    (portal/'portal.css').write_text('body { color: green; }')
    (portal/'main.py').write_text('private backend file')
    monkeypatch.setattr(sec,'ROOT',backend)
    isolated_app=FastAPI();sec.install(isolated_app,{'ALLOWED_ORIGINS':[]})
    with TestClient(isolated_app) as static_client:
        response=static_client.get('/customer/index.html?target=/etc/passwd')
        assert response.status_code==200 and '<!DOCTYPE html>' in response.text
        assert static_client.get('/customer/').status_code==200
        assert static_client.get('/customer/portal.css').status_code==200
        assert static_client.get('/customer/main.py').status_code==404
        assert static_client.get('/customer/../Backend/main.py').status_code==404
        assert response.headers['x-content-type-options']=='nosniff'
        assert 'frame-ancestors' in response.headers['content-security-policy']
        (portal/'portal.css').unlink()
        assert static_client.get('/customer/portal.css').status_code==404
    assert client.post('/auth/signin',headers={'Origin':'https://attacker.example'},json={}).status_code==403


def test_standalone_backend_without_sibling_portals_returns_404(tmp_path, monkeypatch):
    from fastapi import FastAPI
    backend=tmp_path/'backend';backend.mkdir()
    monkeypatch.setattr(sec,'ROOT',backend)
    isolated_app=FastAPI();sec.install(isolated_app,{'ALLOWED_ORIGINS':[]})
    with TestClient(isolated_app) as static_client:
        for prefix in ['customer','admin-portal','driver-portal']:
            assert static_client.get('/'+prefix+'/').status_code==404
            assert static_client.get('/'+prefix+'/index.html').status_code==404


def test_fixture_ignores_inherited_database_url_and_dotenv(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path
    # These are harmless sentinels, never real credentials or a real host.
    (tmp_path/'.env').write_text('DATABASE_URL=postgresql://dotenv-sentinel.invalid/test\nDOTENV_MUST_NOT_LOAD=yes\n')
    script="""
import os, sys
sys.path.insert(0, sys.argv[1])
os.environ['DATABASE_URL']='postgresql://inherited-sentinel.invalid/test'
os.environ['APP_ENV']='production'
import conftest
import dotenv
assert dotenv.load_dotenv('.env') is False
assert 'DOTENV_MUST_NOT_LOAD' not in os.environ
import secure_backend as isolated
assert isolated.DATABASE_URL == ''
assert not isolated.PRODUCTION
assert 'fmv-tests-' in str(isolated.DB_PATH)
def forbidden_connect():
    raise AssertionError('Tests attempted a PostgreSQL connection')
isolated.postgres_connect=forbidden_connect
import main
with isolated.transaction() as state:
    assert state['users'] == {}
print('fixture-isolated')
"""
    result=subprocess.run([sys.executable,'-I','-c',script,str(Path(__file__).resolve().parent)],
                          cwd=tmp_path,env={'PATH':os.defpath},capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr
    assert result.stdout.strip()=='fixture-isolated'


def test_original_schema_definitions_retained():
    schemas=app.openapi()['components']['schemas']
    for name in ['CustomerSignUpRequest','CustomerSignInRequest','RequestOTP','VerifyOTP','ProductCreate','SubscriptionCreate','EPODDeliveryUpdate','CustomerCheckoutRequest','TaxConfigUpdateRequest','B2BTeamMemberInvite']:
        assert name in schemas


def test_pdf_invoice_and_private_document_access():
    customer,u=account();other,_=account('other@example.com',phone='9876543211');admin,_=account('admin@example.com','admin','9876543212')
    checkout(customer)
    invoices=client.get('/portal/billing/invoices',headers=customer).json()['invoices'];iid=invoices[0]['id']
    result=client.get('/portal/billing/invoices/'+iid+'/pdf',headers=customer)
    assert result.status_code==200 and result.content.startswith(b'%PDF-')
    assert client.get('/portal/billing/invoices/'+iid+'/pdf',headers=other).status_code==404
    file=dict(filename='agreement.txt',mime_type='text/plain',data_base64=base64.b64encode(b'Private customer agreement').decode())
    shared=client.post('/admin/documents',headers=admin,json=dict(file,customer_email=u['email'],title='Agreement'))
    assert shared.status_code==200,shared.text
    docs=client.get('/portal/documents',headers=customer).json()['documents'];assert len(docs)==1
    assert client.get('/portal/documents',headers=other).json()['documents']==[]
    fid=docs[0]['file_id']
    assert client.get('/portal/files/'+fid,headers=customer).content==b'Private customer agreement'
    assert client.get('/portal/files/'+fid,headers=other).status_code==404
    assert client.get('/portal/files/'+fid).status_code==401


def test_ticket_attachments_are_owned_and_downloadable():
    a,_=account();b,_=account('second@example.com',phone='9876543211')
    file={'filename':'note.txt','mime_type':'text/plain','data_base64':base64.b64encode(b'Delivery instructions').decode()}
    upload=client.post('/portal/uploads',headers=a,json=file);assert upload.status_code==200
    ident=upload.json()['id']
    body={'subject':'Assistance','category':'delivery','description':'Check instructions','attachments':[ident]}
    assert client.post('/portal/tickets',headers=b,json=body).status_code==404
    assert client.post('/portal/tickets',headers=a,json=body).status_code==200
    assert client.post('/portal/uploads',headers=a,json=dict(file,mime_type='image/svg+xml')).status_code==422


def test_online_unconfigured_and_expired_reservation_release_stock(monkeypatch):
    customer,_=account();payload={'items':[{'product_id':'prod-4','quantity':2}],'payment_method':'Online'}
    monkeypatch.delenv('RAZORPAY_KEY_ID',raising=False);monkeypatch.delenv('RAZORPAY_KEY_SECRET',raising=False)
    h={**customer,'Idempotency-Key':'unconfigured-online-key-123456'}
    assert client.post('/orders/checkout',headers=h,json=payload).status_code==503
    with sec.transaction() as s:assert s['products']['prod-4']['stock_quantity']==250;assert not s['orders']
    monkeypatch.setenv('RAZORPAY_KEY_ID','example');monkeypatch.setenv('RAZORPAY_KEY_SECRET','example')
    response=client.post('/orders/checkout',headers=h,json=payload);assert response.status_code==200
    with sec.transaction() as s:s['orders'][response.json()['order']['id']]['reservation_expires']=time.time()-1
    client.get('/products',headers=customer)
    with sec.transaction() as s:assert s['products']['prod-4']['stock_quantity']==250;assert s['orders'][response.json()['order']['id']]['status']=='Cancelled'


def test_all_original_customer_collections_require_auth_and_work_when_signed_in():
    customer,_=account()
    for path in ['/portal/team','/portal/company-profile','/portal/tickets','/portal/knowledge-base','/portal/forum','/portal/catalog/contract-pricing','/portal/quotes','/portal/rma','/portal/billing/invoices','/portal/documents','/portal/notifications','/portal/enterprise/system-status']:
        assert client.get(path).status_code==401,path
        assert client.get(path,headers=customer).status_code==200,path


def test_validation_does_not_echo_passwords_and_body_limit():
    response=client.post('/auth/signup',json={'email':'invalid','password':'sensitive-password-123'})
    assert response.status_code==422
    assert 'sensitive-password' not in response.text
    response=client.post('/auth/signup',content=b'x'*3000001,headers={'Content-Type':'application/json'})
    assert response.status_code==413
