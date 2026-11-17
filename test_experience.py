"""Regression coverage for v9; email provider calls are always isolated."""
import base64
import io
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from PIL import Image
from fastapi.testclient import TestClient
from main import app
import secure_backend as sec
import experience as exp
from email_templates import build_email, KINDS
from test_secure_flows import account, checkout, photo, PASSWORD, verify, signup

client=TestClient(app)
REAL_SEND_CODE=sec.send_code


def address(headers, **kwargs):
    body=dict(label='Work',recipient='Office Recipient',phone='9876543233',
              address='Office 12, Kochi, Kerala 682001',is_default=False)
    body.update(kwargs)
    return client.post('/users/me/addresses',headers=headers,json=body)


def campaign(headers, **kwargs):
    body=dict(title='Fresh mornings',description='Farm fresh essentials',kind='banner',placement='home')
    body.update(kwargs)
    return client.post('/admin/content',headers=headers,json=body)


def test_saved_addresses_default_checkout_and_snapshot():
    customer,_=account();other,_=account('other@example.com',phone='9876543211')
    original=client.get('/users/me/addresses',headers=customer).json()['addresses']
    assert len(original)==1 and original[0]['is_default']
    saved=address(customer).json()['address']
    response=checkout(customer,address_id=saved['id'],delivery_address='Untrusted override')
    assert response.status_code==200,response.text
    order=response.json()['order']
    assert order['delivery_address']==saved['address']
    assert order['customer_name']=='Office Recipient'
    assert order['customer_phone']=='+919876543233'
    assert checkout(other,address_id=saved['id']).status_code==404
    body={k:saved[k] for k in ['label','recipient','phone','address','is_default']}
    body.update(address='A different work address',is_default=True)
    assert client.patch('/users/me/addresses/'+saved['id'],headers=customer,json=body).status_code==200
    listed=client.get('/users/me/addresses',headers=customer).json()
    assert sum(a['is_default'] for a in listed['addresses'])==1
    assert listed['user']['address']=='A different work address'
    assert client.delete('/users/me/addresses/'+saved['id'],headers=other).status_code==404
    assert client.delete('/users/me/addresses/'+saved['id'],headers=customer).status_code==200
    assert client.get('/orders',headers=customer).json()[0]['delivery_address']==saved['address']
    assert client.get('/users/me/addresses',headers=customer).json()['addresses'][0]['is_default']


def test_address_limit_and_blank_fields():
    h,_=account()
    client.get('/users/me/addresses',headers=h)
    assert address(h,address='   ').status_code==422
    assert address(h,phone='123').status_code==422
    for index in range(19):assert address(h,label='Place '+str(index)).status_code==200
    assert address(h).status_code==409
    assert client.get('/users/me/addresses').status_code==401


def test_purpose_specific_auth_mail_and_default_company_ad(monkeypatch):
    sent=[]
    monkeypatch.setenv('RESEND_API_KEY','isolated-fake-key')
    monkeypatch.setattr(sec,'send_code',REAL_SEND_CODE)
    monkeypatch.setattr(sec.httpx,'post',lambda *args,**kwargs: (sent.append(kwargs['json']) or httpx.Response(200,json={'id':'fake'})))
    assert verify(signup()).status_code==200
    assert 'sign-up' in sent[-1]['subject'] and 'Purpose: Sign up' in sent[-1]['text']
    assert 'FROM FIRST MILK VENTURE' in sent[-1]['text']
    assert '123456' not in sent[-1]['subject']
    assert client.post('/auth/signin',json={'identifier':'buyer@example.com','password':PASSWORD}).status_code==200
    assert 'sign-in' in sent[-1]['subject'] and 'Purpose: Sign in' in sent[-1]['text']
    assert client.post('/auth/password-reset/request',json={'email':'buyer@example.com'}).status_code==200
    assert 'password reset' in sent[-1]['subject'] and 'Purpose: Password Reset' in sent[-1]['text']
    assert all('FROM FIRST MILK VENTURE' in m['html'] for m in sent)


def test_order_events_queue_once_and_send_after_committed_order(monkeypatch):
    customer,_=account();admin,_=account('admin@example.com','admin','9876543211')
    driver,du=account('driver@example.com','driver','9876543212')
    order=checkout(customer).json()['order'];oid=order['id']
    checkout(customer)
    assert client.patch('/admin/deliveries/'+oid+'/assign',headers=admin,json={'driver_key':du['id']}).status_code==200
    for status in ['Shipped','Shipped','Out for Delivery','Out for Delivery']:
        assert client.patch('/admin/deliveries/'+oid+'/status',headers=admin,json={'status':status}).status_code==200
    assert client.post('/driver/shifts/clock-in',headers=driver,json={'vehicle_plate':'KL07AB1234','odometer_start':100,'checklist_json':{'tires':True,'brakes':True,'fuel':True,'cold_chain':True}}).status_code==200
    assert client.patch('/driver/deliveries/'+oid+'/epod',headers=driver,json={'status':'Delivered','delivery_otp':order['delivery_otp'],'epod_photo_url':photo(),'cod_collected':order['cod_amount']}).status_code==200
    with sec.transaction() as state:
        messages=list(state['mail_outbox'].values())
        assert {m['kind'] for m in messages}=={'order_placed','order_shipped','order_out_for_delivery','order_delivered'}
        assert len(messages)==4
    delivered=[]
    def send(item):
        # An independent DB transaction proves network delivery is outside its lock.
        with sec.transaction() as state:assert state['orders'][oid]['status']=='Delivered'
        delivered.append(item)
        return 'provider-'+item['id']
    monkeypatch.setattr(exp,'send_rendered_mail',send)
    exp.drain_mail_outbox();exp.drain_mail_outbox()
    assert len(delivered)==4
    assert all('FROM FIRST MILK VENTURE' in item['content']['text'] for item in delivered)
    assert client.patch('/admin/deliveries/'+oid+'/status',headers=admin,json={'status':'Shipped'}).status_code==409
    status=client.get('/admin/mail-outbox',headers=admin).json()['messages']
    assert all(m['status']=='sent' for m in status)
    assert all('content' not in m for m in status)


def test_provider_failure_retries_without_affecting_stock_or_order(monkeypatch):
    h,_=account();order=checkout(h).json()['order']
    calls=[]
    def fail(item):
        calls.append(item['id']);raise sec.HTTPException(503,'Temporary mail outage')
    monkeypatch.setattr(exp,'send_rendered_mail',fail);exp.drain_mail_outbox()
    with sec.transaction() as state:
        message=next(iter(state['mail_outbox'].values()))
        assert message['status']=='retry' and message['attempts']==1
        assert state['orders'][order['id']]['status']=='Unassigned'
        assert state['products']['prod-4']['stock_quantity']==248
        message['next_attempt']=0
    monkeypatch.setattr(exp,'send_rendered_mail',lambda item: calls.append(item['id']) or 'provider-id')
    exp.drain_mail_outbox();assert calls[0]==calls[1]
    with sec.transaction() as state:assert next(iter(state['mail_outbox'].values()))['status']=='sent'


def test_concurrent_mail_workers_claim_once(monkeypatch):
    h,_=account();checkout(h);sent=[]
    def send(item):
        sent.append(item['id']);time.sleep(.03);return 'provider-id'
    monkeypatch.setattr(exp,'send_rendered_mail',send)
    with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(lambda _:exp.drain_mail_outbox(),range(2)))
    assert len(sent)==1


def test_campaign_permissions_delegation_versions_and_schedule():
    admin,_=account('admin@example.com','admin')
    support,su=account('support@example.com','admin','9876543211',role='customer_support')
    customer,_=account('buyer@example.com',phone='9876543212')
    assert campaign(support).status_code==403
    assert campaign(customer).status_code==403
    assert client.patch('/admin/content-editors/'+su['id'],headers=admin,json={'enabled':True}).status_code==200
    item=campaign(support).json()['campaign']
    assert len(client.get('/content',headers=customer).json()['campaigns'])==1
    assert campaign(admin,status='draft').status_code==200
    future=(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()
    assert campaign(admin,starts_at=future).status_code==200
    assert campaign(admin,placement='email').status_code==200
    assert len(client.get('/content',headers=customer).json()['campaigns'])==1
    assert client.patch('/admin/content/'+item['id'],headers=support,json={'title':'Edited','version':1}).status_code==200
    assert client.patch('/admin/content/'+item['id'],headers=support,json={'title':'Stale','version':1}).status_code==409
    assert campaign(admin,starts_at='2026-11-17T10:00').status_code==422
    assert client.patch('/admin/content-editors/'+su['id'],headers=admin,json={'enabled':False}).status_code==200
    assert client.get('/admin/content',headers=support).status_code==403
    assert client.get('/admin/invitations',headers=support).status_code==403


def test_campaign_media_gif_and_reject_scripts():
    admin,_=account('admin@example.com','admin')
    out=io.BytesIO();frames=[Image.new('RGB',(30,30),color) for color in ['red','green']]
    frames[0].save(out,'GIF',save_all=True,append_images=frames[1:],duration=200,loop=0)
    uploaded=client.post('/admin/content-media',headers=admin,json={'filename':'animation.gif','data_base64':base64.b64encode(out.getvalue()).decode()})
    assert uploaded.status_code==200,uploaded.text
    asset=uploaded.json()
    public=client.get(asset['url']);assert public.status_code==200 and public.content==out.getvalue()
    assert public.headers['content-type']=='image/gif'
    assert campaign(admin,media_id=asset['id']).status_code==200
    assert client.post('/admin/content-media',headers=admin,json={'filename':'ad.svg','data_base64':base64.b64encode(b'<svg onload="alert(1)"></svg>').decode()}).status_code==422
    assert campaign(admin,cta_path='javascript:alert(1)').status_code==422
    assert campaign(admin,media_id='not-a-real-upload').status_code==404


def test_custom_company_ad_in_order_and_auth_emails():
    admin,_=account('admin@example.com','admin');customer,_=account('buyer@example.com',phone='9876543211')
    assert campaign(admin,title='Our dairy collection',placement='email',description='<script>do not execute</script>').status_code==200
    checkout(customer)
    with sec.transaction() as state:
        mail=next(iter(state['mail_outbox'].values()))['content']
    assert 'Our dairy collection' in mail['html']
    assert '<script>' not in mail['html'] and '&lt;script&gt;' in mail['html']
    for kind in KINDS:
        if kind=='offer':continue
        result=build_email(kind,code='123456')
        assert 'Purpose' in result['text'] and 'FROM FIRST MILK VENTURE' in result['html']


def test_invitations_visible_to_super_admin_and_content_manager_signup():
    admin,_=account('admin@example.com','admin')
    response=client.post('/admin/invitations',headers=admin,json={'email':'editor@example.com','role':'content_manager'})
    assert response.status_code==200,response.text
    token=response.json()['invitation']
    public=client.get('/admin/invitations',headers=admin)
    assert token not in public.text
    assert verify(signup('editor@example.com','admin','9876543211',token)).status_code==200
    assert signup('third@example.com','admin','9876543212',token).status_code==403
    editor=verify(client.post('/auth/signin',json={'identifier':'editor@example.com','password':PASSWORD,'portal':'admin'})).json()
    headers={'Authorization':'Bearer '+editor['access_token']}
    assert campaign(headers).status_code==200
    assert client.get('/admin/drivers',headers=headers).status_code==403
    assert client.get('/admin/invitations',headers=headers).status_code==403
    assert client.post('/admin/invitations',headers=admin,json={'email':'bad-address','role':'content_manager'}).status_code==422


def test_company_page_publishing_is_safe_text_and_authorized():
    admin,_=account('admin@example.com','admin');customer,_=account('buyer@example.com',phone='9876543211')
    assert client.get('/pages/terms',headers=customer).json()=={'page':None}
    payload={'title':'Our terms','body':'Company approved content\n<script>example</script>'}
    assert client.patch('/admin/pages/terms',headers=customer,json=payload).status_code==403
    assert client.patch('/admin/pages/terms',headers=admin,json=payload).status_code==200
    assert client.get('/pages/terms',headers=customer).json()['page']['body']==payload['body']
    assert client.patch('/admin/pages/unknown',headers=admin,json=payload).status_code==404


def test_replaced_and_expired_invitations_are_not_emailed(monkeypatch):
    admin,_=account('admin@example.com','admin')
    first=client.post('/admin/invitations',headers=admin,json={'email':'editor@example.com','role':'content_manager'})
    second=client.post('/admin/invitations',headers=admin,json={'email':'editor@example.com','role':'content_manager'})
    assert first.status_code==second.status_code==200
    assert first.json()['invitation']!=second.json()['invitation']
    with sec.transaction() as state:
        messages=list(state['mail_outbox'].values())
        assert messages[0]['status']=='replaced'
        messages[1]['expires_epoch']=time.time()-1
    sent=[];monkeypatch.setattr(exp,'send_rendered_mail',lambda item:sent.append(item))
    exp.drain_mail_outbox()
    assert sent==[]
    with sec.transaction() as state:assert list(state['mail_outbox'].values())[1]['status']=='expired'


def test_invalid_checkout_has_no_mail_side_effects():
    customer,_=account()
    assert checkout(customer,items=[{'product_id':'prod-4','quantity':10000}]).status_code==409
    with sec.transaction() as state:
        assert not state.get('mail_outbox')
        assert not state['orders']
        assert state['products']['prod-4']['stock_quantity']==250

