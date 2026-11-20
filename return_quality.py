"""Yard inspections, conditional customer refunds and evidence-led driver liability.

No automatic customer payouts, product restocking or wage deductions.
Authorization is server-side. All transitions append audit events.
"""
from __future__ import annotations
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal

from fastapi import Request, HTTPException
from pydantic import BaseModel, Field, ConfigDict

import secure_backend as c
import return_collections as pickup
import workforce

class Strict(BaseModel):
    model_config=ConfigDict(extra='forbid')

class QualityRole(Strict):
    quality_manager: bool

class YardReport(Strict):
    finding: Literal['good','not_ok']
    condition: Literal['sealed','opened','damaged','other']
    disposition: Literal['quarantine','inspection_pending','dispose']
    report: str=Field(min_length=20,max_length=1800)
    evidence_reference: str=Field(default='',max_length=180)
    expected_version: int=Field(ge=1)

class RefundAuthorization(Strict):
    amount: Decimal=Field(gt=0,max_digits=12,decimal_places=2)
    rationale: str=Field(min_length=15,max_length=800)
    expected_version: int=Field(ge=1)

class DriverStatement(Strict):
    statement: str=Field(min_length=20,max_length=1800)
    evidence_reference: str=Field(default='',max_length=180)

class Investigation(Strict):
    decision: Literal['driver_fault_proven','not_proven','other_cause']
    evidence_summary: str=Field(min_length=35,max_length=2000)
    evidence_reference: str=Field(default='',max_length=180)
    loss_amount: Decimal=Field(ge=0,max_digits=12,decimal_places=2)
    allow_customer_refund: bool
    expected_version: int=Field(ge=1)

class Recovery(Strict):
    approved_amount: Decimal=Field(gt=0,max_digits=12,decimal_places=2)
    legal_clearance_reference: str=Field(min_length=8,max_length=180)
    hearing_record_reference: str=Field(min_length=8,max_length=180)
    hr_legal_approval_confirmed: bool
    expected_version: int=Field(ge=1)

def is_quality(user):
    return user.get('portal')=='admin' and (
        user.get('role') in {'super_admin','operations_manager'}
        or user.get('driver_manager') is True or user.get('quality_manager') is True)

def quality_user(state, request):
    user,_=c.actor(state,request,'admin')
    c.require(is_quality(user),'An authorized quality or operations reviewer is required.',403)
    return user

def is_finance(user):
    return user.get('portal')=='admin' and (user.get('role') in {'super_admin','finance'}
        or user.get('payout_approver') is True)

def finance_user(state,request):
    user,_=c.actor(state,request,'admin')
    c.require(is_finance(user),'An authorized accounts approver is required.',403)
    return user

def money(value):
    return c.money(value)

def refund_allowed(state, claim, record, amount):
    """Shared precondition for authorizing OR recording a physical return refund."""
    auth=state.get('return_refund_authorizations',{}).get(claim['id'])
    c.require(auth and auth.get('status')=='authorized',
              'Independent accounts authorization is required before a physical return refund.',409)
    c.require(amount<=money(auth['amount']),
              'Refund exceeds the separately authorized amount.',409)
    report=state.get('return_yard_reports',{}).get(claim['id'])
    incident=state.get('return_damage_cases',{}).get(claim['id'])
    if report and report['finding']=='not_ok':
        c.require(incident and incident.get('customer_refund_allowed') is True,
                  'Yard damage case needs an authorized customer refund decision.',409)
    elif report and report['finding']=='good':
        c.require(record.get('status')=='received',
                  'Yard receipt must be confirmed.',409)
    else:
        c.require(record.get('status')=='collected' and record.get('additional_damage')=='none'
                  and record.get('driver_attested_at'),
                  'Early refund requires driver-verified collection with no additional damage.',409)
    return auth

def visible_case(case):
    if not case:return None
    return dict(case)

def driver_case(case):
    if not case:return None
    return {k:case.get(k) for k in ('id','status','created_at','summary',
        'driver_response','driver_response_at','decision','decision_at',
        'evidence_reference','response_deadline')}

def install_return_quality(app):
    with c.transaction() as state:
        for key in ('return_yard_reports','return_damage_cases','return_refund_authorizations'):
            state.setdefault(key,{})

    @app.get('/admin/return-quality',tags=['Yard quality and return decisions'])
    def quality_board(request:Request):
        with c.read_transaction(maintain=False) as state:
            user,_=c.actor(state,request,'admin')
            c.require(is_quality(user) or is_finance(user),
                      'Authorized return quality or finance staff required.',403)
            claims=[]
            for record in state.get('return_collections',{}).values():
                claim=state['rma'].get(record['id'],{})
                if claim.get('claim_type')!='return':continue
                order=state['orders'].get(claim.get('order_id'),{})
                claims.append(dict(id=record['id'],status=record['status'],
                    collection=pickup.public_record(record), claim_status=claim.get('status',''),
                    order_number=order.get('order_number',claim.get('order_id','')),
                    customer=claim.get('owner',''), amount=claim.get('amount','0'),
                    report=state.get('return_yard_reports',{}).get(record['id']),
                    case=visible_case(state.get('return_damage_cases',{}).get(record['id'])),
                    refund_authorization=state.get('return_refund_authorizations',{}).get(record['id'])))
            return {'claims':claims,'can_inspect':is_quality(user),
                'can_authorize_refund':is_finance(user),
                'can_designate':user['role']=='super_admin'}

    @app.get('/admin/return-quality/staff',tags=['Return quality delegates'])
    def list_quality_staff(request:Request):
        with c.read_transaction(maintain=False) as state:
            user,_=c.actor(state,request,'admin')
            c.require(user['role']=='super_admin','Super Admin only.',403)
            return {'staff':[{'id':u['id'],'name':u.get('full_name',''),
                'email':u['email'],'quality_manager':bool(u.get('quality_manager'))}
                for u in state['users'].values() if u.get('portal')=='admin'
                and u.get('approved') and not u.get('deleted')]}

    @app.patch('/admin/return-quality/staff/{staff_id}',tags=['Return quality delegates'])
    def quality_delegate(staff_id:str,request:Request,body:QualityRole):
        with c.transaction() as state:
            actor,_=c.actor(state,request,'admin')
            c.require(actor['role']=='super_admin','Super Admin only.',403)
            subject=next((u for u in state['users'].values() if u.get('id')==staff_id
                and u.get('portal')=='admin' and u.get('approved') and not u.get('deleted')),None)
            c.require(subject,'Staff account not found.',404)
            c.require(subject['id']!=actor['id'],'You cannot modify your own quality designation.',403)
            subject['quality_manager']=body.quality_manager
            c.audit(state,actor['email'],'RETURN_QUALITY_DESIGNATION_UPDATED',subject['id'])
            c.notify(state,subject['email'],'Return quality access updated',
                'Quality inspection permission has been '+('enabled' if body.quality_manager else 'disabled')+
                ' by the Super Admin.',portal='admin')
            return {'id':subject['id'],'quality_manager':body.quality_manager}

    @app.post('/admin/return-quality/{claim_id}/yard-report',tags=['Yard inspection'])
    def inspect_yard(claim_id:str,request:Request,body:YardReport):
        with c.transaction() as state:
            reviewer=quality_user(state,request)
            record=state.get('return_collections',{}).get(claim_id)
            claim=state['rma'].get(claim_id)
            c.require(record and claim and claim.get('claim_type')=='return','Pickup not found.',404)
            c.require(record['status']=='collected','Driver collection must be verified first.',409)
            c.require(record['version']==body.expected_version,'Collection changed; refresh and retry.',409)
            c.require(claim_id not in state['return_yard_reports'],'Yard inspection already completed.',409)
            c.require(body.finding!='not_ok' or bool(body.evidence_reference.strip()),
                      'An evidence reference is required when reporting a damaged item.',422)
            report=dict(id=claim_id,finding=body.finding,condition=body.condition,
                disposition=body.disposition,report=c.clean_text(body.report,1800),
                evidence_reference=c.clean_text(body.evidence_reference,180),
                inspector=reviewer['email'],inspected_at=c.now(),version=1)
            state['return_yard_reports'][claim_id]=report
            record.update(condition_received=body.condition,disposition=body.disposition,
                warehouse_note=report['report'],received_at=c.now(),received_by=reviewer['email'],
                version=record['version']+1)
            if body.finding=='good':
                record['status']='received'
                if claim['status']=='approved':
                    claim.update(status='received',received_at=record['received_at'])
                c.audit(state,reviewer['email'],'RETURN_YARD_GOOD',claim_id)
                pickup.notify(state,claim,'Return quality verified',
                    'Your return reached the yard and passed the product inspection.')
            else:
                record['status']='quality_hold'
                case=dict(id=claim_id,status='awaiting_driver_response',
                    driver_id=record['driver_id'],driver_name=record['driver_name'],
                    created_at=c.now(),reporter=reviewer['email'],version=1,
                    department='quality_compliance',summary=report['report'],
                    evidence_reference=report['evidence_reference'],
                    customer_refund_allowed=False,
                    response_deadline=(datetime.now(c.IST)+timedelta(hours=48)).isoformat())
                state['return_damage_cases'][claim_id]=case
                c.audit(state,reviewer['email'],'RETURN_YARD_DAMAGE_ESCALATED',claim_id)
                c.notify(state,claim['owner'],'Returned item under quality review',
                    'The yard identified a condition discrepancy and is reviewing the returned item.',portal='customer')
                driver=state['drivers'].get(record['driver_id'],{})
                if driver.get('email'):
                    c.notify(state,driver['email'],'Return condition review: response requested',
                        'A discrepancy was reported for return '+claim_id+
                        '. You can submit your explanation and evidence in Pickups.',portal='driver')
                workforce.notify_managers(state,'Return quality incident escalated',
                    'Inspect return '+claim_id+' and review evidence and driver response.')
            return {'report':report,'collection':pickup.public_record(record),
                'case':visible_case(state.get('return_damage_cases',{}).get(claim_id))}

    @app.post('/admin/return-quality/{claim_id}/refund-authorization',tags=['Early customer refund approval'])
    def authorize_refund(claim_id:str,request:Request,body:RefundAuthorization):
        with c.transaction() as state:
            reviewer=finance_user(state,request)
            claim=state['rma'].get(claim_id)
            record=state.get('return_collections',{}).get(claim_id)
            c.require(claim and record and claim.get('claim_type')=='return',
                      'Physical return record not found.',404)
            c.require(record['version']==body.expected_version,
                      'Return status changed; refresh and review.',409)
            c.require(record['status'] in {'collected','received','quality_hold'},
                      'Verified collection must occur before refund authorization.',409)
            c.require(record.get('additional_damage')=='none' and record.get('driver_attested_at'),
                      'Driver must confirm no additional damage at pickup.',409)
            c.require(money(body.amount)<=money(claim['amount']),
                      'Refund cannot exceed the claim amount.',422)
            report=state['return_yard_reports'].get(claim_id)
            case=state['return_damage_cases'].get(claim_id)
            c.require(not report or report['finding']=='good' or (case and case.get('customer_refund_allowed')),
                      'Yard discrepancy requires an independent refund-eligibility decision.',409)
            c.require(claim['status'] in {'approved','received'},
                      'A reviewed physical return is required.',409)
            c.require(claim_id not in state['return_refund_authorizations'],
                      'Refund was already authorized for this return.',409)
            authorization=dict(id=claim_id,status='authorized',amount=str(body.amount),
                rationale=c.clean_text(body.rationale,800),
                authorized_by=reviewer['email'],authorized_at=c.now())
            state['return_refund_authorizations'][claim_id]=authorization
            c.audit(state,reviewer['email'],'RETURN_REFUND_AUTHORIZED',claim_id)
            pickup.notify(state,claim,'Refund approved for processing',
                'Your return refund was approved for processing. This is not payment confirmation.')
            return {'authorization':authorization,
                    'message':'Refund eligibility authorized. No funds have been transferred.'}

    @app.get('/driver/return-quality',tags=['Driver damage case response'])
    def assigned_cases(request:Request):
        with c.read_transaction(maintain=False) as state:
            driver,_=c.actor(state,request,'driver')
            return {'cases':[driver_case(case) for case in state['return_damage_cases'].values()
                if case['driver_id']==driver['id']]}

    @app.post('/driver/return-quality/{claim_id}/response',tags=['Driver damage case response'])
    def respond_case(claim_id:str,request:Request,body:DriverStatement):
        with c.transaction() as state:
            driver,_=c.actor(state,request,'driver')
            case=state['return_damage_cases'].get(claim_id)
            c.require(case and case['driver_id']==driver['id'],
                      'Assigned quality incident not found.',404)
            c.require(case['status']=='awaiting_driver_response',
                      'The response window for this incident has closed.',409)
            c.require(not case.get('driver_response'),'A response is already recorded.',409)
            case.update(driver_response=c.clean_text(body.statement,1800),
                driver_response_reference=c.clean_text(body.evidence_reference,180),
                driver_response_at=c.now(),version=case['version']+1)
            c.audit(state,driver['email'],'RETURN_DRIVER_RESPONSE',claim_id)
            workforce.notify_managers(state,'Driver responded to quality incident',
                'Return '+claim_id+' has a new driver explanation.')
            return {'case':driver_case(case)}

    @app.post('/admin/return-quality/{claim_id}/investigation',tags=['Independent damage investigation'])
    def investigate(claim_id:str,request:Request,body:Investigation):
        with c.transaction() as state:
            investigator=quality_user(state,request)
            case=state['return_damage_cases'].get(claim_id)
            c.require(case,'Escalated return case not found.',404)
            c.require(case['status']=='awaiting_driver_response',
                      'This case has already been investigated.',409)
            c.require(case['version']==body.expected_version,
                      'Case changed; refresh and review.',409)
            c.require(investigator['email']!=case['reporter'],
                      'The yard inspector cannot independently decide the allegation.',403)
            c.require(case.get('driver_response') or datetime.now(c.IST)>=datetime.fromisoformat(case['response_deadline']),
                      'Allow the driver to respond before deciding the case.',409)
            if body.decision=='driver_fault_proven':
                c.require(body.loss_amount>0 and len(body.evidence_reference.strip())>=8,
                          'A proven-loss decision needs an amount and verifiable evidence reference.',422)
            else:
                c.require(body.loss_amount==0,'Do not record driver losses without a proven decision.',422)
            claim=state['rma'][claim_id]
            c.require(body.loss_amount<=Decimal(str(claim.get('amount',0))),
                      'Proposed loss exceeds the recorded return value.',422)
            case.update(decision=body.decision,
                evidence_summary=c.clean_text(body.evidence_summary,2000),
                investigation_reference=c.clean_text(body.evidence_reference,180),
                proposed_loss=str(body.loss_amount),
                customer_refund_allowed=body.allow_customer_refund,
                decided_by=investigator['email'],decision_at=c.now(),
                status='finance_review' if body.decision=='driver_fault_proven' else 'closed',
                version=case['version']+1)
            c.audit(state,investigator['email'],'RETURN_CASE_INVESTIGATED',claim_id)
            driver=state['drivers'].get(case['driver_id'],{})
            if driver.get('email'):
                c.notify(state,driver['email'],'Return investigation update',
                    'A quality investigation decision has been recorded for '+claim_id+
                    '. Contact administration if you wish to dispute the decision.',portal='driver')
            workforce.notify_managers(state,'Return case decision',
                'Return '+claim_id+' has been investigated.',financial=True)
            return {'case':visible_case(case)}

    @app.post('/admin/return-quality/{claim_id}/recovery',tags=['Legally cleared driver receivable adjustment'])
    def recover(claim_id:str,request:Request,body:Recovery):
        with c.transaction() as state:
            reviewer=finance_user(state,request)
            case=state['return_damage_cases'].get(claim_id)
            c.require(case and case.get('status')=='finance_review'
                      and case.get('decision')=='driver_fault_proven',
                      'Only independently proven and open cases can reach finance recovery.',409)
            c.require(case['version']==body.expected_version,
                      'Case was changed; refresh and retry.',409)
            c.require(reviewer['email'] not in {case['reporter'],case['decided_by']},
                      'A different accounts approver must independently review recovery.',403)
            c.require(body.hr_legal_approval_confirmed,
                      'Record independent HR/legal approval before any wage or receivable deduction.',409)
            cents=money(body.approved_amount)
            c.require(cents<=money(case['proposed_loss']),
                      'Recovery cannot exceed the substantiated actual loss.',422)
            available=money(workforce.wallet(state,case['driver_id'])['available_to_claim'])
            # Conservative single-period cap. More restrictive applicable labour
            # limits must be reviewed by HR; no negative earnings or retroactive debit.
            c.require(cents<=available//2 and cents>0,
                      'Recovery cannot exceed half of the current available earnings.',409)
            driver=state['drivers'].get(case['driver_id'],{})
            c.require(driver and not driver.get('deleted'),
                      'Driver receivable is unavailable.',409)
            reference='CASE:'+claim_id+':'+c.clean_text(body.legal_clearance_reference,180)
            # Unique source avoids duplicate debits after retries.
            earning=workforce.add_earning(state,case['driver_id'],'deduction',cents,
                reference,'return-quality:'+claim_id,reviewer['email'])
            case.update(status='recovery_recorded',recovered_amount=str(body.approved_amount),
                recovery_id=earning['id'],recovery_at=c.now(),
                recovery_by=reviewer['email'],
                legal_reference=c.clean_text(body.legal_clearance_reference,180),
                hearing_reference=c.clean_text(body.hearing_record_reference,180),
                version=case['version']+1)
            c.audit(state,reviewer['email'],'RETURN_RECOVERY_RECORDED',claim_id)
            if driver.get('email'):
                c.notify(state,driver['email'],'Reviewed return loss adjustment',
                    'A reviewed receivable adjustment was recorded for case '+claim_id+
                    '. Contact Accounts/HR for the supporting record or dispute process.',portal='driver')
            return {'case':visible_case(case),
                'message':'Independently approved adjustment recorded in the driver receivable ledger.'}
