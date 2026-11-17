"""Security and ledger regressions for v11. No live mail, maps or payments."""
import base64
import json
import uuid
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient
from main import app
import secure_backend as sec
from email_templates import build_email
from mail_routing import sender_for
from test_secure_flows import account, checkout, photo
from test_portal_fixes import delivered_cash

client=TestClient(app)

def seed_earning(admin,driver_id,amount=300,key='verified-earnings-reference-123456'):
    return client.post('/admin/driver-earnings',headers={**admin,'Idempotency-Key':key},json={'driver_id':driver_id,'category':'base_pay','amount':amount,'reference':'Verified payroll voucher 001'})

def payout(driver,amount=100,key='driver-payout-request-123456'):
    return client.post('/driver/payouts',headers={**driver,'Idempotency-Key':key},json={'amount':amount,'method':'cash'})

def campaign(admin,driver_id,**extra):
    return client.post('/admin/incentives',headers={**admin,'Idempotency-Key':uuid.uuid4().hex},json=dict(title='Safe deliveries',instructions='Complete the assigned checklist and delivery reports.',reward=30,target_deliveries=0,driver_ids=[driver_id],**extra))

def claim(driver,campaign_id):
    return client.post('/driver/incentives/'+campaign_id+'/claims',headers=driver,json={'note':'Checklist and reports completed.','completed':True})

def test_subscription_lifecycle_mail_once_per_real_transition():
    customer,_=account()
    created=client.post('/subscriptions',headers=customer,json={'product_id':'prod-4','quantity':1,'frequency':'Daily','start_date':sec.today(),'zone_id':'Zone-1'})
    assert created.status_code==200,created.text
    ident=created.json()['subscription']['id']
    for status in ['paused','paused','active','active','cancelled','cancelled']:
        assert client.patch('/subscriptions/'+ident,headers=customer,json={'status':status}).status_code==200
    assert client.patch('/subscriptions/'+ident,headers=customer,json={'status':'active'}).status_code==409
    with sec.read_transaction() as state:
        mails=list(state['mail_outbox'].values())
        assert len(mails)==4
        assert {m['kind'] for m in mails}=={'subscription_created','subscription_paused','subscription_resumed','subscription_cancelled'}
        assert all('FROM FIRST MILK VENTURE' in m['content']['html'] and 'Purpose:' in m['content']['text'] for m in mails)
        assert all('orders@' in sender_for(m['kind']) for m in mails)

def test_email_codes_are_contiguous_selectable_and_purpose_specific():
    for kind in ['signup_verification','signin_verification','password_reset']:
        mail=build_email(kind,code='123456')
        assert '123456' in mail['html'] and mail['text'].startswith('123456 is your First Milk verification code.')
        assert 'user-select:all' in mail['html'] and 'letter-spacing:5px' not in mail['html']
        assert '123456' not in mail['subject']

def test_preferences_are_individual_and_strictly_validated():
    one,_=account();two,_=account('other@example.com',phone='9876543211')
    assert client.patch('/users/me/preferences',headers=one,json={'dark_mode':True,'notification_sound':True}).status_code==200
    assert client.get('/portal/snapshot',headers=one).json()['preferences']['dark_mode'] is True
    assert client.get('/portal/snapshot',headers=two).json()['preferences']['dark_mode'] is False
    assert client.patch('/users/me/preferences',headers=one,json={'dark_mode':'true','notification_sound':False}).status_code==422

def test_failed_attempt_is_scoped_idempotent_and_keeps_running_order():
    customer,admin,driver,user,_=delivered_cash()
    order=checkout(customer,key='attempt-running-order-123456').json()['order']
    client.patch('/admin/deliveries/'+order['id']+'/assign',headers=admin,json={'driver_key':user['id']})
    path='/driver/deliveries/'+order['id']+'/attempts';body={'reason':'Customer unavailable','note':'Called customer; agreed to retry.'}
    headers={**driver,'Idempotency-Key':'failed-delivery-attempt-123456'}
    first=client.post(path,headers=headers,json=body);assert first.status_code==200,first.text
    assert client.post(path,headers=headers,json=body).status_code==200
    assert client.post(path,headers=customer,json=body).status_code==403
    with sec.read_transaction() as state:
        assert len(state['delivery_attempts'])==1 and state['orders'][order['id']]['status']=='Assigned'
        assert any(n['title']=='Delivery attempt unsuccessful' for n in state['notifications'].values())

def test_incentive_completion_needs_assignment_and_review_before_credit():
    _,admin,driver,user,_=delivered_cash();other,other_user=account('second-driver@example.com','driver','9876543213')
    created=campaign(admin,user['id']);assert created.status_code==200,created.text
    ident=created.json()['id']
    assert claim(other,ident).status_code==404
    submitted=claim(driver,ident);assert submitted.status_code==200,submitted.text
    claim_id=submitted.json()['claim']['id']
    assert claim(driver,ident).json()['claim']['id']==claim_id
    assert not client.get('/portal/snapshot',headers=driver).json()['workforce']['wallet']['entries']
    path='/admin/incentive-claims/'+claim_id
    assert client.patch(path,headers=driver,json={'status':'approved'}).status_code==403
    for _ in range(2):assert client.patch(path,headers=admin,json={'status':'approved'}).status_code==200
    entries=client.get('/portal/snapshot',headers=driver).json()['workforce']['wallet']['entries']
    assert len(entries)==1 and entries[0]['amount']==30

def test_delivery_target_and_closed_instruction_cannot_be_bypassed():
    _,admin,driver,user,_=delivered_cash()
    body=dict(title='Five new deliveries',instructions='Complete five new deliveries.',reward=50,target_deliveries=5,driver_ids=[user['id']])
    item=client.post('/admin/incentives',headers={**admin,'Idempotency-Key':uuid.uuid4().hex},json=body).json()
    assert claim(driver,item['id']).status_code==409
    body.update(title='Closed instructions',target_deliveries=0)
    item=client.post('/admin/incentives',headers={**admin,'Idempotency-Key':uuid.uuid4().hex},json=body).json()
    assert client.post('/admin/incentives/'+item['id']+'/close',headers=admin).status_code==200
    assert claim(driver,item['id']).status_code==409

def test_base_and_peak_pay_are_recorded_once_at_verified_delivery():
    customer,_=account();admin,_=account('admin@example.com','admin','9876543211');driver,user=account('driver@example.com','driver','9876543212')
    assert client.patch('/admin/drivers/'+user['id']+'/pay-rule',headers=admin,json={'base_pay':20,'peak_bonus':5,'peak_hours':list(range(24))}).status_code==200
    order=checkout(customer).json()['order']
    client.patch('/admin/deliveries/'+order['id']+'/assign',headers=admin,json={'driver_key':user['id']})
    client.post('/driver/shifts/clock-in',headers=driver,json={'vehicle_plate':'KL07AB1234','odometer_start':100,'checklist_json':{'tires':True,'brakes':True,'fuel':True,'cold_chain':True}})
    body={'status':'Delivered','delivery_otp':order['delivery_otp'],'epod_photo_url':photo(),'cod_collected':order['cod_amount']}
    path='/driver/deliveries/'+order['id']+'/epod'
    assert client.patch(path,headers=driver,json=body).status_code==200
    assert client.patch(path,headers=driver,json=body).status_code==409
    balance=client.get('/portal/snapshot',headers=driver).json()['workforce']['wallet']
    assert balance['breakdown']['base_pay']==20 and balance['breakdown']['peak_bonus']==5
    assert len(balance['entries'])==2 and balance['net_earnings']==-95

def test_wallet_cash_deduction_reservation_and_payout_state_machine():
    _,admin,driver,user,_=delivered_cash()
    assert seed_earning(admin,user['id']).status_code==200
    assert seed_earning(admin,user['id']).status_code==200
    assert client.get('/portal/snapshot',headers=driver).json()['workforce']['wallet']['available_to_claim']==180
    request=payout(driver);assert request.status_code==200,request.text
    assert payout(driver).json()['id']==request.json()['id']
    assert payout(driver,100,key='second-payout-overspend-123456').status_code==409
    path='/admin/driver-payouts/'+request.json()['id']
    assert client.patch(path,headers=driver,json={'status':'approved'}).status_code==403
    assert client.patch(path,headers=admin,json={'status':'paid','reference':'BANK01','confirmed_paid':True}).status_code==409
    assert client.patch(path,headers=admin,json={'status':'approved'}).status_code==200
    assert client.patch(path,headers=admin,json={'status':'paid'}).status_code==422
    for _ in range(2):assert client.patch(path,headers=admin,json={'status':'paid','reference':'BANK01','confirmed_paid':True}).status_code==200
    balance=client.get('/portal/snapshot',headers=driver).json()['workforce']['wallet']
    assert balance['paid_out']==100 and balance['cash_balance_deduction']==120 and balance['available_to_claim']==80

def test_concurrent_payout_requests_do_not_double_reserve():
    _,admin,driver,user,_=delivered_cash();seed_earning(admin,user['id'])
    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses=list(pool.map(lambda n:payout(driver,150,key='concurrent-wallet-reservation-'+str(n)).status_code,[1,2]))
    assert sorted(statuses)==[200,409]

def test_payout_is_rechecked_if_new_cod_arrives_after_approval():
    customer,admin,driver,user,_=delivered_cash();seed_earning(admin,user['id'])
    request=payout(driver,150).json();path='/admin/driver-payouts/'+request['id']
    assert client.patch(path,headers=admin,json={'status':'approved'}).status_code==200
    order=checkout(customer,key='new-cod-after-approval-123456').json()['order']
    client.patch('/admin/deliveries/'+order['id']+'/assign',headers=admin,json={'driver_key':user['id']})
    assert client.patch('/driver/deliveries/'+order['id']+'/epod',headers=driver,json={'status':'Delivered',
        'delivery_otp':order['delivery_otp'],'epod_photo_url':photo(),'cod_collected':order['cod_amount']}).status_code==200
    assert client.patch(path,headers=admin,json={'status':'paid','reference':'PAY01','confirmed_paid':True}).status_code==409
    balance=client.get('/portal/snapshot',headers=driver).json()['workforce']['wallet']
    assert balance['paid_out']==0 and balance['cash_balance_deduction']==240

def test_incentive_publication_retry_does_not_duplicate_campaigns():
    _,admin,_,user,_=delivered_cash()
    headers={**admin,'Idempotency-Key':'publish-instructions-retry-123456'}
    body=dict(title='One campaign',instructions='Complete the checklist.',reward=20,target_deliveries=0,driver_ids=[user['id']])
    first=client.post('/admin/incentives',headers=headers,json=body)
    second=client.post('/admin/incentives',headers=headers,json=body)
    assert first.status_code==200 and first.json()['id']==second.json()['id']
    with sec.read_transaction() as state:assert len(state['driver_incentives'])==1

def test_delegation_requires_superadmin_and_does_not_grant_other_modules():
    _,admin,driver,user,_=delivered_cash();support,su=account('support@example.com','admin','9876543213',role='customer_support')
    path='/admin/workforce/delegates/'+su['id'];body={'driver_manager':True,'payout_approver':False}
    assert client.patch(path,headers=support,json=body).status_code==403
    assert campaign(support,user['id']).status_code==403
    assert client.patch(path,headers=admin,json=body).status_code==200
    assert campaign(support,user['id']).status_code==200
    assert seed_earning(support,user['id']).status_code==403
    assert client.get('/admin/products',headers=support).status_code in {403,405}
    assert client.patch(path,headers=admin,json={'driver_manager':False,'payout_approver':False}).status_code==200
    assert campaign(support,user['id']).status_code==403

def test_document_wallet_expiration_private_download_and_authorization():
    customer,admin,driver,user,_=delivered_cash();other,_=account('other-driver@example.com','driver','9876543213')
    raw=b'%PDF-1.4\nTest-only vehicle document\n%%EOF'
    body={'kind':'Insurance','filename':'insurance.pdf','expires_on':'2026-01-01','data_base64':base64.b64encode(raw).decode()}
    uploaded=client.post('/driver/documents',headers=driver,json=body);assert uploaded.status_code==200,uploaded.text
    path='/driver-documents/'+uploaded.json()['id']
    assert client.get(path,headers=driver).content==raw
    assert client.get(path,headers=admin).status_code==200
    assert client.get(path,headers=other).status_code==404
    assert client.get(path,headers=customer).status_code==404
    assert client.get(path).status_code==401
    assert client.get(path,headers=driver).headers['cache-control']=='no-store'
    snapshot=client.get('/portal/snapshot',headers=driver).json()
    assert snapshot['workforce']['documents'][0]['status']=='expired'
    assert 'data_base64' not in json.dumps(snapshot['workforce']['documents'])
    assert client.post('/driver/documents',headers=driver,json=dict(body,expires_on='2099-01-01')).status_code==200
    renewed=client.get('/portal/snapshot',headers=driver).json()['workforce']['documents']
    assert renewed[0]['status']=='superseded' and renewed[1]['status']=='valid'
    assert client.get(path,headers=driver).content==raw
    body.update(filename='evil.svg',data_base64=base64.b64encode(b'<svg onload="alert(1)"/>').decode())
    assert client.post('/driver/documents',headers=driver,json=body).status_code==422

def test_order_chat_has_ownership_reassignment_and_closed_order_guards():
    customer,admin,driver,user,_=delivered_cash();other,_=account('outsider@example.com',phone='9876543213')
    order=checkout(customer,key='chat-running-order-123456789').json()['order']
    client.patch('/admin/deliveries/'+order['id']+'/assign',headers=admin,json={'driver_key':user['id']})
    path='/orders/'+order['id']+'/chat';headers={**customer,'Idempotency-Key':'order-chat-message-key-123456'}
    for _ in range(2):assert client.post(path,headers=headers,json={'message':'Please ring the bell.'}).status_code==200
    assert len(client.get(path,headers=driver).json()['messages'])==1
    assert client.get(path,headers=other).status_code==404
    assert client.post(path,headers=other,json={'message':'Forbidden'}).status_code==404
    replacement,replacement_user=account('replacement@example.com','driver','9876543214')
    client.patch('/admin/deliveries/'+order['id']+'/assign',headers=admin,json={'driver_key':replacement_user['id']})
    assert client.get(path,headers=driver).status_code==404
    assert client.get(path,headers=replacement).status_code==200
    with sec.transaction() as state:state['orders'][order['id']]['status']='Cancelled'
    assert client.post(path,headers=headers,json={'message':'No more messages'}).status_code==409

def test_customer_ratings_are_real_unique_and_order_scoped():
    customer,admin,driver,user,order=delivered_cash();other,_=account('outsider@example.com',phone='9876543213')
    path='/orders/'+order['id']+'/rating'
    assert client.post(path,headers=other,json={'stars':5}).status_code==404
    assert client.post(path,headers=driver,json={'stars':5}).status_code==403
    for stars in [5,3]:assert client.post(path,headers=customer,json={'stars':stars,'comment':'Thanks'}).status_code==200
    performance=client.get('/portal/snapshot',headers=driver).json()['workforce']['performance']
    assert performance['rating_count']==1 and performance['rating']==3

def test_route_locations_costs_and_no_driver_profit_leakage():
    customer,admin,driver,user,_=delivered_cash()
    order=checkout(customer,key='routing-new-order-123456').json()['order']
    client.patch('/admin/deliveries/'+order['id']+'/assign',headers=admin,json={'driver_key':user['id']})
    assert client.get('/driver/route',headers=driver).json()['status']=='needs_locations'
    assert client.patch('/admin/route-settings',headers=admin,json={'latitude':9.5,'longitude':76.5,'cost_per_km':5,'cost_per_stop':2,'unit_costs':{'prod-4':40}}).status_code==200
    assert client.patch('/admin/deliveries/'+order['id']+'/location',headers=admin,json={'latitude':9.51,'longitude':76.51}).status_code==200
    private=client.get('/driver/route',headers=driver).json();public=client.get('/admin/routes/'+user['id'],headers=admin).json()
    assert private['status']=='ready' and len(private['stops'])==1 and private['directions_urls']
    assert 'profit' not in json.dumps(private).lower() and 'unit_cost' not in json.dumps(private).lower()
    assert public['estimated_profit'] is not None and public['cost_data_complete']
    assert client.get('/admin/routes/'+user['id'],headers=driver).status_code==403
    assert client.patch('/admin/route-settings',headers=driver,json={'latitude':9.5,'longitude':76.5,'cost_per_km':5,'cost_per_stop':2}).status_code==403

def test_saved_coordinates_are_owned_and_snapshotted_into_checkout():
    customer,_=account();other,_=account('other@example.com',phone='9876543211')
    address=client.get('/users/me/addresses',headers=customer).json()['addresses'][0]
    path='/users/me/addresses/'+address['id']+'/location'
    assert client.patch(path,headers=other,json={'latitude':9.5,'longitude':76.5}).status_code==404
    assert client.patch(path,headers=customer,json={'latitude':9.5,'longitude':76.5}).status_code==200
    assert client.patch(path,headers=customer,json={'latitude':91,'longitude':76.5}).status_code==422
    order=checkout(customer,address_id=address['id']).json()['order']
    assert order['delivery_latitude']==9.5 and order['delivery_longitude']==76.5
