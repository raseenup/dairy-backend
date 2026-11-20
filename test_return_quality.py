"""Quality inspection, conditional refunds, investigations and driver recovery tests."""
import pytest
import secure_backend as c
import workforce
from test_secure_flows import client, account
from test_return_collections import delivered_order, claim_for, approve, assign


def setup_case(extra_damage='none'):
    customer,_=account()
    manager,_=account('manager@example.com','admin','9876543211',role='operations_manager')
    investigator,_=account('inspector2@example.com','admin','9876543212',role='operations_manager')
    finance,_=account('accounts@example.com','admin','9876543213',role='finance')
    driver,driver_user=account('driver@example.com','driver','9876543214')
    order=delivered_order(customer)
    claim=claim_for(customer,order)
    approve(manager,claim)
    assert assign(manager,claim,driver_user['id']).status_code==200
    pin=client.get('/customer/return-collections',headers=customer).json()['pickups'][0]['pickup_pin']
    data={'pickup_pin':pin,'condition':'sealed','additional_damage':extra_damage,
        'attestation_confirmed':extra_damage=='none',
        'note':'No additional damage during pickup.' if extra_damage=='none' else 'New damage was identified at collection.'}
    result=client.post('/driver/return-collections/'+claim['id']+'/collect',
        headers=driver,json=data)
    assert result.status_code==200,result.text
    return dict(customer=customer,manager=manager,investigator=investigator,finance=finance,
                driver=driver,driver_user=driver_user,order=order,claim=claim)


def version(claim_id):
    with c.read_transaction(maintain=False) as s:
        return s['return_collections'][claim_id]['version']


def yard(case,finding='good',inspector=None):
    cl=case['claim']['id']
    return client.post('/admin/return-quality/'+cl+'/yard-report',
        headers=inspector or case['manager'],json={
          'finding':finding,'condition':'sealed' if finding=='good' else 'damaged',
          'disposition':'quarantine',
          'report':'Item checked at yard; evidence confirms the reported product condition.',
          'evidence_reference':'SECURE-QA-PHOTO-REF-001' if finding=='not_ok' else '',
          'expected_version':version(cl)})


def early_auth(case):
    cl=case['claim']['id']
    return client.post('/admin/return-quality/'+cl+'/refund-authorization',
        headers=case['finance'],json={
          'amount':str(case['claim']['amount']),
          'rationale':'Returned item verified and customer refund authorized for processing.',
          'expected_version':version(cl)})


def test_early_refund_requires_driver_attestation_and_independent_accounts_approval():
    case=setup_case()
    cid=case['claim']['id']
    refund_url='/admin/returns/'+cid+'/refund-record'
    payload={'method':'manual','amount':str(case['claim']['amount']),
             'refund_reference':'BANK-EVIDENCE-REF-ABC-01'}
    assert client.post(refund_url,headers=case['finance'],json=payload).status_code==409
    authorized=early_auth(case)
    assert authorized.status_code==200,authorized.text
    assert authorized.json()['authorization']['status']=='authorized'
    assert early_auth(case).status_code==409
    # Authorization is not a payment. Manual bank transfers are not provider verified.
    refunded=client.post(refund_url,headers=case['finance'],json=payload)
    assert refunded.status_code==200,refunded.text
    assert refunded.json()['claim']['status']=='manual_refund_reported'
    report=yard(case)
    assert report.status_code==200,report.text
    with c.read_transaction(maintain=False) as s:
        assert s['rma'][cid]['status']=='manual_refund_reported'
        assert s['return_collections'][cid]['status']=='received'
        assert s['return_yard_reports'][cid]['finding']=='good'


def test_early_refund_is_blocked_when_driver_reports_extra_damage():
    case=setup_case('observed')
    assert early_auth(case).status_code==409
    assert client.get('/admin/return-quality',headers=case['customer']).status_code==403
    assert client.get('/driver/return-quality',headers=case['driver']).json()['cases']==[]


def test_yard_discrepancy_escalates_and_requires_driver_response_and_independent_review():
    case=setup_case()
    cid=case['claim']['id']
    res=yard(case,'not_ok')
    assert res.status_code==200,res.text
    assert res.json()['collection']['status']=='quality_hold'
    assert early_auth(case).status_code==409
    driver_cases=client.get('/driver/return-quality',headers=case['driver']).json()['cases']
    assert len(driver_cases)==1
    assert driver_cases[0]['id']==cid
    # No immediate wage deduction or automatic driver fault.
    with c.read_transaction(maintain=False) as s:
        assert not s.get('driver_earnings')
    p='/admin/return-quality/'+cid+'/investigation'
    evidence={'decision':'driver_fault_proven',
        'evidence_summary':'Independent review of yard condition, pickup record and supporting evidence establishes responsibility.',
        'evidence_reference':'EVIDENCE-PACK-REF-1234',
        'loss_amount':'10.00','allow_customer_refund':True,'expected_version':1}
    assert client.post(p,headers=case['investigator'],json=evidence).status_code==409
    statement=client.post('/driver/return-quality/'+cid+'/response',
        headers=case['driver'],json={
          'statement':'I collected an intact sealed item and documented its condition at pickup.',
          'evidence_reference':'DRIVER-CAMERA-REF-135'})
    assert statement.status_code==200,statement.text
    evidence['expected_version']=2
    assert client.post(p,headers=case['manager'],json=evidence).status_code==403
    approved=client.post(p,headers=case['investigator'],json=evidence)
    assert approved.status_code==200,approved.text
    assert approved.json()['case']['status']=='finance_review'
    with c.transaction() as s:
        workforce.add_earning(s,case['driver_user']['id'],'base_pay',20000,
                              'Valid delivery compensation','test:payroll-base', 'system')
    rec_url='/admin/return-quality/'+cid+'/recovery'
    payload={'approved_amount':'10.00','legal_clearance_reference':'HR-LEGAL-CLEAR-11',
        'hearing_record_reference':'HR-HEARING-REF-11','hr_legal_approval_confirmed':False,
        'expected_version':3}
    assert client.post(rec_url,headers=case['finance'],json=payload).status_code==409
    payload['hr_legal_approval_confirmed']=True
    res=client.post(rec_url,headers=case['finance'],json=payload)
    assert res.status_code==200,res.text
    assert res.json()['case']['status']=='recovery_recorded'
    assert client.post(rec_url,headers=case['finance'],json=payload).status_code==409
    with c.read_transaction(maintain=False) as s:
        deductions=[e for e in s['driver_earnings'].values() if e['category']=='deduction']
        assert len(deductions)==1 and deductions[0]['amount_paise']==1000
        assert s['return_damage_cases'][cid]['recovery_by']=='accounts@example.com'


def test_quality_report_good_does_not_allow_finance_by_anonymous_or_manager():
    case=setup_case('uncertain')
    cid=case['claim']['id']
    assert early_auth(case).status_code==409
    assert yard(case).status_code==200
    assert client.post('/admin/return-quality/'+cid+'/refund-authorization',
        headers=case['manager'],json={
            'amount':'5.00','rationale':'I confirm return refund for customer.',
            'expected_version':version(cid)}).status_code==403
    assert early_auth(case).status_code==200


def test_negative_report_requires_evidence_and_blocks_duplicate_inspections():
    case=setup_case()
    cid=case['claim']['id']
    bad=client.post('/admin/return-quality/'+cid+'/yard-report',
        headers=case['manager'],json={'finding':'not_ok','condition':'damaged',
          'disposition':'quarantine','report':'Item arrived with significant damage and unusual marks.',
          'evidence_reference':'','expected_version':version(cid)})
    assert bad.status_code==422
    assert yard(case,'not_ok').status_code==200
    assert yard(case,'good').status_code==409
    other_driver,_=account('otherdriver@example.com','driver','9876543215')
    assert client.get('/driver/return-quality',headers=other_driver).json()['cases']==[]


def test_super_admin_can_delegate_and_revoke_yard_quality_only():
    super_token,_=account('super@example.com','admin','9876543212',role='super_admin')
    staff_token,staff=account('quality@example.com','admin','9876543222',role='customer_support')
    url='/admin/return-quality/staff/'+staff['id']
    assert client.get('/admin/return-quality',headers=staff_token).status_code==403
    assert client.patch(url,headers=staff_token,json={'quality_manager':True}).status_code==403
    result=client.patch(url,headers=super_token,json={'quality_manager':True})
    assert result.status_code==200,result.text
    assert client.get('/admin/return-quality',headers=staff_token).status_code==200
    assert client.patch(url,headers=super_token,json={'quality_manager':False}).status_code==200
    assert client.get('/admin/return-quality',headers=staff_token).status_code==403
