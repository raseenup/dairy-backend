"""Secure, audited item-return pickup management for FMV.

Additive endpoints. Customer-only pickup code, assigned-driver collection,
designated manager receipt and finance-only refund authorization stay separate.
"""
from __future__ import annotations

import hmac
import hashlib
import secrets
from datetime import date, timedelta
from typing import Literal

from fastapi import Request, HTTPException
from pydantic import BaseModel, ConfigDict, Field

import secure_backend as c

DEFAULTS = {
    'enabled': True,
    'pickup_window_days': 7,
    'max_active_pickups_per_driver': 15,
    'max_pin_attempts': 5,
    'instructions': 'Keep the returned dairy item safely packaged. Give the collection PIN only when the authorized driver arrives.',
    'version': 1
}

class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')

class CollectionSettings(StrictModel):
    enabled: bool
    pickup_window_days: int = Field(ge=1, le=30)
    max_active_pickups_per_driver: int = Field(ge=1, le=60)
    instructions: str = Field(min_length=10, max_length=800)
    version: int = Field(ge=1)

class ReviewRequest(StrictModel):
    status: Literal['approved','rejected']
    note: str = Field(default='', max_length=700)

class Assignment(StrictModel):
    driver_id: str = Field(min_length=1,max_length=100)
    scheduled_date: date
    note: str = Field(default='',max_length=700)
    expected_version: int = Field(ge=0)

class CollectProof(StrictModel):
    pickup_pin: str = Field(pattern=r'^\d{6}$')
    condition: Literal['sealed','opened','damaged','other']
    note: str = Field(default='',max_length=700)

class WarehouseReceipt(StrictModel):
    condition: Literal['sealed','opened','damaged','other']
    disposition: Literal['quarantine','dispose','inspection_pending']
    note: str = Field(default='',max_length=700)

class CancelCollection(StrictModel):
    reason: str = Field(min_length=8,max_length=700)

def settings(state):
    return {**DEFAULTS,**state.get('return_collection_settings',{})}

def manager(state, request):
    user,_=c.actor(state,request,'admin')
    c.require(user['role'] in {'super_admin','operations_manager'}
              or user.get('driver_manager') is True,
              'Only operations administrators or designated driver managers can manage returns.',403)
    return user

def public_record(record):
    return {k:v for k,v in record.items() if k not in {'pin_nonce','pin_attempts'}}

def derive_pin(record):
    # No plain PIN is stored. HMAC is only used for this owner-scoped
    # six-digit pickup credential and never replaces session authorization.
    material=('return-pickup:'+record['id']+':'+record['pin_nonce']).encode()
    digest=hmac.new(c.SECRET.encode(),material,hashlib.sha256).digest()
    return f'{int.from_bytes(digest[:8],"big")%1000000:06d}'

def customer_view(state, claim):
    record=state.get('return_collections',{}).get(claim['id'])
    if not record:return None
    result={k:record.get(k) for k in (
        'id','status','scheduled_date','driver_name','assigned_at',
        'collected_at','received_at','condition','disposition')}
    result['instructions']=settings(state)['instructions']
    # Never show the pickup code after collection, cancellation or receipt.
    if record['status']=='scheduled':
        result['pickup_pin']=derive_pin(record)
    return result

def driver_view(state, record):
    claim=state['rma'].get(record['id'],{})
    order=state['orders'].get(claim.get('order_id'),{})
    return {
        **public_record(record),
        'order_number':order.get('order_number',claim.get('order_id','')),
        'customer_name':order.get('customer_name',''),
        'customer_phone':order.get('customer_phone',''),
        'pickup_address':order.get('delivery_address',''),
        'items_summary':order.get('items_summary',''),
        'instructions':settings(state)['instructions']
    }

def notify(state, claim, title, message):
    c.notify(state,claim['owner'],title,message,portal='customer')

def install_return_collections(app):
    # For old databases, keep every existing claim and all schemas intact.
    with c.transaction() as state:
        state.setdefault('return_collections',{})
        state.setdefault('return_collection_settings',dict(DEFAULTS))

    @app.get('/admin/return-collections',tags=['Return pickup operations'])
    def staff_collections(request:Request):
        with c.read_transaction(maintain=False) as state:
            manager(state,request)
            drivers=[
                {'id':d['id'],'name':d.get('name','Driver'),'zone':d.get('zone','')}
                for d in state['drivers'].values()
                if d.get('approved') and not d.get('deleted') and d.get('email')
                and state['users'].get('driver:'+d['email'],{}).get('approved')
            ]
            claims=[]
            for claim in state['rma'].values():
                if claim.get('claim_type') not in {'return','refund'}:continue
                collection=state.get('return_collections',{}).get(claim['id'])
                order=state['orders'].get(claim.get('order_id'),{})
                claims.append({
                    'id':claim['id'],'order_id':claim['order_id'],
                    'claim_type':claim.get('claim_type','return'),
                    'status':claim['status'],'reason':claim.get('reason',''),
                    'created_at':claim.get('created_at',''),
                    'customer_email':claim.get('owner',''),
                    'order_number':order.get('order_number',claim['order_id']),
                    'pickup_address':order.get('delivery_address',''),
                    'collection':public_record(collection) if collection else None
                })
            return {'settings':settings(state),'claims':claims,'drivers':drivers}

    @app.patch('/admin/return-collections/settings',tags=['Return pickup operations'])
    def update_settings(request:Request,body:CollectionSettings):
        with c.transaction() as state:
            user=manager(state,request)
            current=settings(state)
            c.require(body.version==current['version'],
                      'Settings were changed by another manager. Refresh and retry.',409)
            text=c.clean_text(body.instructions,800)
            c.require(len(text)>=10,'Please provide clear collection instructions.',422)
            updated={**body.model_dump(),'instructions':text,
                'max_pin_attempts':5,'version':current['version']+1,
                'updated_at':c.now(),'updated_by':user['email']}
            state['return_collection_settings']=updated
            c.audit(state,user['email'],'RETURN_COLLECTION_SETTINGS_UPDATED',str(updated['version']))
            return {'settings':updated}

    @app.post('/admin/return-collections/{claim_id}/review',tags=['Return pickup operations'])
    def review(claim_id:str,request:Request,body:ReviewRequest):
        with c.transaction() as state:
            user=manager(state,request)
            claim=state['rma'].get(claim_id)
            c.require(claim,'Return request not found.',404)
            c.require(claim['status']=='under_review','This request has already been reviewed.',409)
            claim['status']=body.status
            claim['reviewed_at']=c.now()
            claim['reviewed_by']=user['email']
            claim['review_note']=c.clean_text(body.note,700)
            c.audit(state,user['email'],'RETURN_'+body.status.upper(),claim_id)
            notify(state,claim,'Return request update',
                   'Your request '+claim_id+' was '+body.status+'.')
            return {'claim':claim}

    @app.post('/admin/return-collections/{claim_id}/assign',tags=['Return pickup operations'])
    def assign(claim_id:str,request:Request,body:Assignment):
        with c.transaction() as state:
            user=manager(state,request)
            cfg=settings(state)
            c.require(cfg['enabled'],'Return pickups are disabled by management.',409)
            claim=state['rma'].get(claim_id)
            c.require(claim and claim.get('claim_type')=='return',
                      'Only physical return requests can be assigned for collection.',404)
            c.require(claim['status']=='approved','Approve the return before assigning a pickup.',409)
            driver=state['drivers'].get(body.driver_id)
            c.require(driver and driver.get('approved') and driver.get('email')
                      and not driver.get('deleted')
                      and state['users'].get('driver:'+driver['email'],{}).get('approved'),
                      'Choose an approved, active driver.',422)
            delta=(body.scheduled_date-date.today()).days
            c.require(0<=delta<=cfg['pickup_window_days'],
                      'Schedule a pickup within the configured number of days.',422)
            collection=state['return_collections'].get(claim_id)
            c.require(not collection or collection['status'] in {'scheduled','cancelled'},
                      'Collected items cannot be reassigned.',409)
            current_version=collection.get('version',0) if collection else 0
            c.require(body.expected_version==current_version,
                      'This pickup changed. Refresh before editing.',409)
            count=sum(p.get('driver_id')==body.driver_id
                      and p.get('status')=='scheduled' and p['id']!=claim_id
                      for p in state['return_collections'].values())
            c.require(count<cfg['max_active_pickups_per_driver'],
                      'This driver has reached their active pickup limit.',409)
            if collection and collection.get('driver_id')!=body.driver_id:
                old=state['drivers'].get(collection.get('driver_id'),{})
                if old.get('email'):
                    c.notify(state,old['email'],'Return pickup reassigned',
                             'Pickup '+claim_id+' is no longer assigned to you.',portal='driver')
            record=dict(id=claim_id,order_id=claim['order_id'],driver_id=driver['id'],
                driver_name=driver.get('name','Driver'),status='scheduled',
                scheduled_date=body.scheduled_date.isoformat(),
                note=c.clean_text(body.note,700),pin_nonce=secrets.token_hex(16),
                pin_attempts=0,version=current_version+1,
                assigned_by=user['email'],assigned_at=c.now())
            state['return_collections'][claim_id]=record
            c.audit(state,user['email'],'RETURN_PICKUP_ASSIGNED',claim_id)
            c.notify(state,driver['email'],'Return collection assigned',
                     'Pickup '+claim_id+' is scheduled for '+record['scheduled_date']+'.',
                     portal='driver')
            notify(state,claim,'Return pickup arranged',
                   'Your return collection is scheduled for '+record['scheduled_date']+
                   '. Your pickup PIN is available in your secure account.')
            return {'collection':public_record(record)}

    @app.post('/admin/return-collections/{claim_id}/cancel',tags=['Return pickup operations'])
    def cancel(claim_id:str,request:Request,body:CancelCollection):
        with c.transaction() as state:
            user=manager(state,request)
            record=state['return_collections'].get(claim_id)
            c.require(record and record['status']=='scheduled',
                      'Only an uncollected pickup may be cancelled.',409)
            record.update(status='cancelled',cancelled_at=c.now(),
                          cancelled_by=user['email'],cancel_reason=c.clean_text(body.reason,700),
                          version=record['version']+1)
            record['pin_nonce']=secrets.token_hex(16)
            c.audit(state,user['email'],'RETURN_PICKUP_CANCELLED',claim_id)
            claim=state['rma'][claim_id]
            notify(state,claim,'Return pickup cancelled','Pickup '+claim_id+
                   ' was cancelled. Contact support if you need a new collection date.')
            driver=state['drivers'].get(record['driver_id'],{})
            if driver.get('email'):c.notify(state,driver['email'],
                'Return pickup cancelled','Pickup '+claim_id+' was cancelled.',portal='driver')
            return {'collection':public_record(record)}

    @app.get('/customer/return-collections',tags=['Customer collection tracking'])
    def customer_collections(request:Request):
        with c.read_transaction(maintain=False) as state:
            user,_=c.actor(state,request,'customer')
            return {'pickups':[
                customer_view(state,claim) for claim in c.mine(state,'rma',user)
                if customer_view(state,claim) is not None]}

    @app.get('/driver/return-collections',tags=['Driver return pickups'])
    def driver_collections(request:Request):
        with c.read_transaction(maintain=False) as state:
            user,_=c.actor(state,request,'driver')
            driver=state['drivers'].get(user['id'])
            c.require(driver and driver.get('approved') and not driver.get('deleted'),
                      'Your driver account is not currently approved.',403)
            return {'pickups':[
                driver_view(state,item) for item in state['return_collections'].values()
                if item.get('driver_id')==user['id']
                   and item.get('status') in {'scheduled','collected'}]}

    @app.post('/driver/return-collections/{claim_id}/collect',tags=['Driver return pickups'])
    def collect(claim_id:str,request:Request,body:CollectProof):
        with c.read_transaction(maintain=False) as snapshot:
            user,_=c.actor(snapshot,request,'driver')
            driver=snapshot['drivers'].get(user['id'])
            c.require(driver and driver.get('approved') and not driver.get('deleted'),
                      'Your driver account is not currently approved.',403)
        # Avoid database write lock contention from repeated invalid codes.
        c.rate_limit('pickup:'+user['id']+':'+claim_id,9,3600)
        failure=None
        with c.transaction() as state:
            user,_=c.actor(state,request,'driver')
            record=state['return_collections'].get(claim_id)
            c.require(record and record.get('driver_id')==user['id'],
                      'Assigned collection not found.',404)
            c.require(record['status']=='scheduled','This pickup is not pending collection.',409)
            c.require(record['pin_attempts']<settings(state)['max_pin_attempts'],
                      'Pickup PIN locked. Ask the manager to reassign and issue a new code.',423)
            if not hmac.compare_digest(derive_pin(record),body.pickup_pin):
                record['pin_attempts']+=1
                c.audit(state,user['email'],'RETURN_PICKUP_CODE_FAILED',claim_id)
                failure='Incorrect pickup PIN. Check the code with the customer.'
            else:
                record.update(status='collected',collected_at=c.now(),
                    condition=body.condition,driver_note=c.clean_text(body.note,700),
                    collection_verified_by=user['email'],version=record['version']+1)
                # Expire the old code immediately; nothing financial is changed.
                record['pin_nonce']=secrets.token_hex(16)
                c.audit(state,user['email'],'RETURN_ITEM_COLLECTED',claim_id)
                claim=state['rma'][claim_id]
                notify(state,claim,'Return item collected',
                       'Driver collection was recorded for '+claim_id+
                       '. Warehouse receipt and refund review remain separate.')
        if failure:raise HTTPException(422,failure)
        return {'collection':public_record(record),
                'message':'Pickup verified. Warehouse receipt is still required.'}

    @app.post('/admin/return-collections/{claim_id}/receive',tags=['Warehouse receipts'])
    def receive(claim_id:str,request:Request,body:WarehouseReceipt):
        with c.transaction() as state:
            user=manager(state,request)
            record=state['return_collections'].get(claim_id)
            c.require(record and record['status']=='collected',
                      'A driver-verified pickup is required before warehouse receipt.',409)
            claim=state['rma'].get(claim_id)
            c.require(claim and claim['status']=='approved',
                      'This return is not awaiting warehouse receipt.',409)
            record.update(status='received',condition_received=body.condition,
                disposition=body.disposition,warehouse_note=c.clean_text(body.note,700),
                received_at=c.now(),received_by=user['email'],
                version=record['version']+1)
            claim.update(status='received',received_at=record['received_at'])
            c.audit(state,user['email'],'RETURN_WAREHOUSE_RECEIVED',claim_id)
            notify(state,claim,'Returned item received',
                   'Warehouse receipt of your returned item '+claim_id+
                   ' has been recorded. Any refund remains subject to finance review.')
            return {'collection':public_record(record),'claim':claim,
                    'message':'Warehouse receipt recorded. No item was automatically restocked or refunded.'}
