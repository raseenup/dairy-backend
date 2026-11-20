"""Secure return collection end-to-end authorization and state transition tests.

Uses conftest.py disposable local database. Never contacts payment providers.
"""
from datetime import date

import pytest
from pydantic import ValidationError

import secure_backend as sec
import return_collections as pick
from test_secure_flows import client, account, checkout


def delivered_order(customer):
    order=checkout(customer).json()['order']
    with sec.transaction() as state:
        state['orders'][order['id']]['status']='Delivered'
        state['orders'][order['id']]['payment_status']='cod_collected'
    return order


def claim_for(customer,order,kind='return'):
    result=client.post('/customer/returns',headers=customer,json={
        'order_id':order['id'],'request_type':kind,
        'reason':'The product packaging was damaged at delivery.'})
    assert result.status_code==200,result.text
    return result.json()['claim']


def approve(manager,claim):
    result=client.post('/admin/return-collections/'+claim['id']+'/review',
        headers=manager,json={'status':'approved','note':'Approved for return assessment'})
    assert result.status_code==200,result.text


def assign(manager,claim,driver_id,version=0):
    return client.post('/admin/return-collections/'+claim['id']+'/assign',
        headers=manager,json={'driver_id':driver_id,'scheduled_date':sec.today(),
                              'expected_version':version,'note':'Collect at front door.'})


def yard(manager,claim,condition='sealed'):
    with sec.read_transaction(maintain=False) as state:
        version=state['return_collections'][claim['id']]['version']
    return client.post('/admin/return-quality/'+claim['id']+'/yard-report',
        headers=manager,json={'finding':'good','condition':condition,
            'disposition':'quarantine',
            'report':'Product received intact at yard. Condition checked.',
            'expected_version':version})


def test_return_collection_rejects_unauthorized_roles_and_allows_designated_manager():
    customer,_=account()
    staff,_=account('support@example.com','admin','9876543211',role='customer_support')
    finance,_=account('finance@example.com','admin','9876543212',role='finance')
    assert client.get('/admin/return-collections',headers=customer).status_code==403
    assert client.get('/admin/return-collections').status_code==401
    assert client.get('/admin/return-collections',headers=finance).status_code==403
    assert client.get('/admin/return-collections',headers=staff).status_code==403
    with sec.transaction() as state:
        state['users']['admin:support@example.com']['driver_manager']=True
    assert client.get('/admin/return-collections',headers=staff).status_code==200
    cfg=client.get('/admin/return-collections',headers=staff).json()['settings']
    assert cfg['enabled'] is True
    response=client.patch('/admin/return-collections/settings',headers=staff,json={
        'enabled':True,'pickup_window_days':10,'max_active_pickups_per_driver':12,
        'instructions':'Collect only from approved order addresses.', 'version':cfg['version']})
    assert response.status_code==200,response.text
    assert client.patch('/admin/return-collections/settings',headers=staff,json={
        'enabled':True,'pickup_window_days':10,'max_active_pickups_per_driver':12,
        'instructions':'Collect only from approved order addresses.', 'version':cfg['version']}).status_code==409


def test_assigned_pickup_protects_customer_code_driver_identity_and_warehouse():
    customer,_=account()
    manager,_=account('operations@example.com','admin','9876543211',role='operations_manager')
    other,_=account('outside@example.com','customer','9876543212')
    driver,driver_user=account('driver@example.com','driver','9876543213')
    other_driver,_=account('otherdriver@example.com','driver','9876543214')
    order=delivered_order(customer);claim=claim_for(customer,order)
    # Only approved returns are assigned.
    assert assign(manager,claim,driver_user['id']).status_code==409
    approve(manager,claim)
    assert client.post('/admin/return-collections/'+claim['id']+'/receive',
        headers=manager,json={'condition':'sealed','disposition':'quarantine'}).status_code==409
    result=assign(manager,claim,driver_user['id'])
    assert result.status_code==200,result.text
    record=result.json()['collection']
    assert record['status']=='scheduled'
    assert 'pin_nonce' not in record and 'pickup_pin' not in record
    assert assign(manager,claim,driver_user['id']).status_code==409  # stale version

    customer_pickups=client.get('/customer/return-collections',headers=customer).json()['pickups']
    assert len(customer_pickups)==1
    pin=customer_pickups[0]['pickup_pin']
    assert len(pin)==6 and pin.isdigit()
    assert client.get('/customer/return-collections',headers=other).json()['pickups']==[]
    manager_view=client.get('/admin/return-collections',headers=manager).json()
    assert pin not in str(manager_view)
    assert 'pin_nonce' not in str(manager_view)
    assignment=client.get('/driver/return-collections',headers=driver).json()['pickups']
    assert len(assignment)==1 and assignment[0]['id']==claim['id']
    assert 'pickup_pin' not in str(assignment) and 'pin_nonce' not in str(assignment)
    assert client.get('/driver/return-collections',headers=other_driver).json()['pickups']==[]
    assert client.post('/driver/return-collections/'+claim['id']+'/collect',
        headers=other_driver,json={'pickup_pin':pin,'condition':'sealed'}).status_code==404

    # Only an actual assigned driver can collect. Wrong code is persisted.
    invalid='000000' if pin!='000000' else '000001'
    response=client.post('/driver/return-collections/'+claim['id']+'/collect',
        headers=driver,json={'pickup_pin':invalid,'condition':'sealed'})
    assert response.status_code==422
    with sec.read_transaction(maintain=False) as state:
        assert state['return_collections'][claim['id']]['pin_attempts']==1
    success=client.post('/driver/return-collections/'+claim['id']+'/collect',
        headers=driver,json={'pickup_pin':pin,'condition':'sealed','note':'Item packed.'})
    assert success.status_code==200,success.text
    assert success.json()['collection']['status']=='collected'
    assert client.post('/driver/return-collections/'+claim['id']+'/collect',
        headers=driver,json={'pickup_pin':pin,'condition':'sealed'}).status_code==409
    assert client.get('/customer/return-collections',headers=customer).json()['pickups'][0].get('pickup_pin') is None
    assert client.post('/admin/return-collections/'+claim['id']+'/receive',headers=manager,
        json={'condition':'sealed','disposition':'quarantine'}).status_code==409
    receipt=yard(manager,claim)
    assert receipt.status_code==200,receipt.text
    with sec.read_transaction(maintain=False) as state:
        assert state['rma'][claim['id']]['status']=='received'
    with sec.read_transaction(maintain=False) as state:
        assert state['products']['prod-4']['stock_quantity']==248


def test_pin_lockout_and_reassignment_rotates_code():
    customer,_=account()
    manager,_=account('operations@example.com','admin','9876543211',role='operations_manager')
    driver,driver_user=account('driver@example.com','driver','9876543213')
    order=delivered_order(customer);claim=claim_for(customer,order)
    approve(manager,claim)
    result=assign(manager,claim,driver_user['id'])
    assert result.status_code==200
    pin=client.get('/customer/return-collections',headers=customer).json()['pickups'][0]['pickup_pin']
    invalid='000000' if pin!='000000' else '000001'
    for _ in range(5):
        assert client.post('/driver/return-collections/'+claim['id']+'/collect',
            headers=driver,json={'pickup_pin':invalid,'condition':'sealed'}).status_code==422
    assert client.post('/driver/return-collections/'+claim['id']+'/collect',
        headers=driver,json={'pickup_pin':pin,'condition':'sealed'}).status_code==423
    second=assign(manager,claim,driver_user['id'],version=result.json()['collection']['version'])
    assert second.status_code==200,second.text
    new_code=client.get('/customer/return-collections',headers=customer).json()['pickups'][0]['pickup_pin']
    with sec.read_transaction(maintain=False) as state:
        assert state['return_collections'][claim['id']]['pin_attempts']==0
    # Extremely unlikely HMAC collision (6 digits); acceptance is tested instead.
    assert client.post('/driver/return-collections/'+claim['id']+'/collect',
        headers=driver,json={'pickup_pin':new_code,'condition':'sealed'}).status_code==200


def test_refund_cannot_bypass_return_item_pickup():
    customer,_=account()
    manager,_=account('operations@example.com','admin','9876543211',role='operations_manager')
    finance,_=account('finance@example.com','admin','9876543212',role='finance')
    driver,driver_user=account('driver@example.com','driver','9876543213')
    order=delivered_order(customer);claim=claim_for(customer,order)
    approve(manager,claim)
    url='/admin/returns/'+claim['id']+'/refund-record'
    body={'method':'manual','refund_reference':'verified-bank-manual-REF-12345','amount':'60.00'}
    assert client.post(url,headers=finance,json=body).status_code==409
    assert client.patch('/admin/returns/'+claim['id'],headers=finance,json={'status':'received'}).status_code==409
    assert assign(manager,claim,driver_user['id']).status_code==200
    pin=client.get('/customer/return-collections',headers=customer).json()['pickups'][0]['pickup_pin']
    assert client.post('/driver/return-collections/'+claim['id']+'/collect',
        headers=driver,json={'pickup_pin':pin,'condition':'opened'}).status_code==200
    assert client.post(url,headers=finance,json=body).status_code==409
    assert yard(manager,claim,'opened').status_code==200
    assert client.post(url,headers=finance,json=body).status_code==409
    with sec.read_transaction(maintain=False) as state:
        version=state['return_collections'][claim['id']]['version']
    auth=client.post('/admin/return-quality/'+claim['id']+'/refund-authorization',
        headers=finance,json={'amount':'60.00','rationale':'Item received and checked at yard.',
                              'expected_version':version})
    assert auth.status_code==200,auth.text
    result=client.post(url,headers=finance,json=body)
    assert result.status_code==200,result.text
    assert result.json()['claim']['status']=='manual_refund_reported'
    assert client.get('/admin/return-collections',headers=customer).status_code==403


def test_refund_only_claim_does_not_generate_physical_pickup():
    customer,_=account()
    manager,_=account('operations@example.com','admin','9876543211',role='operations_manager')
    driver,driver_user=account('driver@example.com','driver','9876543213')
    claim=claim_for(customer,delivered_order(customer),kind='refund')
    approve(manager,claim)
    assert assign(manager,claim,driver_user['id']).status_code==404
    assert client.get('/customer/return-collections',headers=customer).json()['pickups']==[]
    assert pick.settings({})['max_pin_attempts']==5


def test_invalid_settings_and_pickup_codes_rejected():
    with pytest.raises(ValidationError):
        pick.CollectionSettings(enabled=True,pickup_window_days=99,max_active_pickups_per_driver=5,
                                instructions='Collect safely.',version=1)
    with pytest.raises(ValidationError):
        pick.CollectProof(pickup_pin='1234',condition='sealed')
