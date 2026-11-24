"""Driver work, approval ledgers, private documents, preferences and order chat.

All authority comes from live individual sessions. Monetary values are integer
paise; no browser can award itself earnings or confirm a payout.
"""
from __future__ import annotations
import base64
import io
import json
import re
import time
from datetime import date, datetime
from typing import Literal

from fastapi import HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from PIL import Image


def core():
    import secure_backend
    return secure_backend


def staff(state, request, financial=False):
    c=core();user,_=c.actor(state,request,'admin')
    roles={'super_admin','finance'} if financial else {'super_admin','operations_manager'}
    flag='payout_approver' if financial else 'driver_manager'
    c.require(user['role'] in roles or user.get(flag) is True,'A designated '+('accounts approver' if financial else 'driver manager')+' is required.',403)
    return user


def may_manage(user, financial=False):
    return user['portal']=='admin' and (user['role'] in ({'super_admin','finance'} if financial else {'super_admin','operations_manager'})
            or user.get('payout_approver' if financial else 'driver_manager') is True)


def notify_managers(state,title,message,financial=False):
    for user in state['users'].values():
        if user.get('approved') and may_manage(user,financial):
            core().notify(state,user['email'],title,message,portal='admin')


def driver_record(state, ident):
    driver=state['drivers'].get(ident)
    core().require(driver and driver.get('email'),'Registered driver not found.',404)
    return driver


def subscription_email(state,user,sub,event):
    from experience import queue_email
    queue_email(state,'subscription:'+sub['id']+':'+str(sub.get('version',1)),user['email'],'subscription_'+event,
        name=user['full_name'],details=[('Subscription',sub['id']),('Product',sub['product_name']),
        ('Quantity per delivery',sub['quantity']),('Frequency',sub['frequency']),('Start date',sub['start_date']),('Status',sub['status'])],
        message='Subscription changes affect future order generation. Orders already placed keep their recorded status; manage them in My Orders or contact support.')
    core().notify(state,user['email'],'Subscription '+event,sub['product_name']+': '+sub['status'])


def add_earning(state,driver_id,kind,amount_paise,reference,source,actor):
    records=state.setdefault('driver_earnings',{})
    existing=next((e for e in records.values() if e['source']==source),None)
    if existing:return existing
    c=core();item=dict(id=c.uid('EARN'),driver_id=driver_id,category=kind,amount_paise=amount_paise,
        amount=amount_paise/100,reference=reference,source=source,created_at=c.now(),created_by=actor)
    records[item['id']]=item;c.audit(state,actor,'DRIVER_EARNING_RECORDED',item['id'])
    return item


def record_delivery_earnings(state,order):
    from payroll import rule_for
    c=core();ident=order['assigned_driver_id'];rule=rule_for(state,ident,str(order['delivered_time'])[:10])
    for kind,amount in [('base_pay',rule.get('base_pay_paise',0)),('peak_bonus',rule.get('peak_bonus_paise',0))]:
        if kind=='base_pay' and rule.get('pay_basis','per_delivery')!='per_delivery':continue
        if kind=='peak_bonus' and datetime.fromisoformat(order['delivered_time']).astimezone(c.IST).hour not in rule.get('peak_hours',[]):continue
        if amount:add_earning(state,ident,kind,amount,'Delivery '+order['id'],'delivery:'+order['id']+':'+kind,'system')


def wallet(state,driver_id):
    from operations import driver_cash
    from payroll import schedule_view
    earnings=[e for e in state.get('driver_earnings',{}).values() if e['driver_id']==driver_id]
    requests=[p for p in state.get('driver_payouts',{}).values() if p['driver_id']==driver_id]
    totals={key:sum(e['amount_paise'] for e in earnings if e['category']==key) for key in
            ['base_pay','tips','peak_bonus','incentive','other','deduction']}
    gross=sum(v for k,v in totals.items() if k!='deduction')
    paid=sum(p['amount_paise'] for p in requests if p['status']=='paid')
    reserved=sum(p['amount_paise'] for p in requests if p['status'] in {'pending','approved'})
    cash_hold=core().money(driver_cash(state,driver_id)['net_cash_to_remit'])
    net=gross-totals['deduction']-paid-cash_hold
    return dict(currency='INR',breakdown={k:v/100 for k,v in totals.items()},gross_earnings=gross/100,
        deductions=totals['deduction']/100,cash_balance_deduction=cash_hold/100,paid_out=paid/100,
        net_earnings=net/100,pending_payouts=reserved/100,available_to_claim=max(0,net-reserved)/100,
        entries=earnings,requests=requests,**schedule_view(state,driver_id))


def performance(state,driver_id):
    orders=[o for o in state['orders'].values() if o.get('assigned_driver_id')==driver_id]
    delivered=[o for o in orders if o['status']=='Delivered']
    attempts=[a for a in state.get('delivery_attempts',{}).values() if a['driver_id']==driver_id]
    ratings=[r for r in state.get('delivery_ratings',{}).values() if r['driver_id']==driver_id]
    on_time=sum(str(o.get('delivered_time',''))[:10]<=o['delivery_date'] for o in delivered)
    return dict(delivered=len(delivered),running=sum(o['status'] not in {'Delivered','Failed','Cancelled','Awaiting Payment'} for o in orders),
        failed_orders=sum(o['status']=='Failed' for o in orders),failed_attempts=len(attempts),
        on_time_percent=round(100*on_time/len(delivered),1) if delivered else None,
        rating=round(sum(r['stars'] for r in ratings)/len(ratings),2) if ratings else None,rating_count=len(ratings),
        reviews=[{k:r[k] for k in ('stars','comment','created_at')} for r in ratings],
        completed_shifts=sum(s['driver_id']==driver_id and s['status']=='completed' for s in state['shifts'].values()))


def incentive_view(state,driver_id):
    result=[]
    for campaign in state.get('driver_incentives',{}).values():
        if driver_id not in campaign['driver_ids']:continue
        claim=next((r for r in state.get('incentive_claims',{}).values() if r['campaign_id']==campaign['id'] and r['driver_id']==driver_id),None)
        count=sum(o.get('assigned_driver_id')==driver_id and o['status']=='Delivered'
                  and campaign['created_at']<=o.get('delivered_time','') for o in state['orders'].values())
        allowed=campaign['active'] and bool(campaign['instructions'].strip()) and count>=campaign['target_deliveries'] and not claim
        result.append(dict(campaign,completed_deliveries=count,claim=claim,can_submit=allowed))
    return result


def document_view(state,driver_id):
    result=[];today=date.fromisoformat(core().today())
    for item in state.get('driver_documents',{}).values():
        if item['driver_id']!=driver_id:continue
        days=(date.fromisoformat(item['expires_on'])-today).days
        result.append(dict(item,days_until_expiry=days,status='superseded' if item.get('superseded_by') else 'expired' if days<0 else 'expires_soon' if days<=30 else 'valid'))
    return result


def chat_access(state,user,order_id,write=False):
    c=core();order=state['orders'].get(order_id);c.require(order,'Order not found.',404)
    if user['portal']=='customer':allowed=order['owner']==user['email']
    elif user['portal']=='driver':allowed=order.get('assigned_driver_id')==user['id']
    else:allowed=may_manage(user) or user['role']=='customer_support'
    c.require(allowed,'Order conversation not found.',404)
    if write:c.require(order['status'] not in {'Cancelled','Delivered','Failed'},'This conversation is closed. Contact support for a closed order.',409)
    return order


def chat_badges(state,user):
    result={}
    for order_id,messages in state.get('order_chats',{}).items():
        order=state['orders'].get(order_id,{})
        if not ((user['portal']=='customer' and order.get('owner')==user['email']) or
                (user['portal']=='driver' and order.get('assigned_driver_id')==user['id'])):continue
        seen=state.get('chat_reads',{}).get(user['id']+':'+order_id,0)
        result[order_id]=sum(m['sequence']>seen and m['sender_id']!=user['id'] for m in messages)
    return result


def extend_snapshot(state,user,result,request):
    prefs=state.get('portal_preferences',{}).get(user['id'],dict(dark_mode=False,notification_sound=False))
    result['preferences']=prefs;result['chat_unread']=chat_badges(state,user)
    if user['portal']=='driver':
        from routing import route_plan
        result['workforce']=dict(wallet=wallet(state,user['id']),performance=performance(state,user['id']),
            incentives=incentive_view(state,user['id']),documents=document_view(state,user['id']),
            vehicle=state.get('driver_vehicles',{}).get(user['id'],{}),route=route_plan(state,user['id'],False),
            attempts=[a for a in state.get('delivery_attempts',{}).values() if a['driver_id']==user['id']])
    elif user['portal']=='admin':
        result['notifications']=[n for n in core().mine(state,'notifications',user) if n.get('portal')=='admin'][-100:]
        result['workforce_permissions']=dict(manage=may_manage(user),finance=may_manage(user,True))
        if (may_manage(user) or may_manage(user,True)) and request.query_params.get('workforce')=='true':
            result['workforce']={}
            if may_manage(user):
                from routing import route_plan
                result['workforce']['overtime_requests'] = [
                    dict(id=sh['id'], driver_name=sh['driver_name'], clock_in=sh['clock_in'],
                         clock_out=sh['clock_out'], extra_seconds=sh['overtime_review']['extra_seconds'])
                    for sh in state['shifts'].values() if sh.get('overtime_review', {}).get('status') == 'pending'
                ]
                result['workforce'].update(route_products=[{k:p.get(k,'') for k in ('id','name','unit')} for p in state['products'].values() if not p.get('deleted')],
                    drivers=[dict(id=d['id'],name=d['name'],email=d.get('email',''),approved=bool(d.get('approved')),
                    performance=performance(state,d['id']),documents=document_view(state,d['id']))
                    for d in state['drivers'].values() if not d.get('deleted')],
                    incentives=list(state.get('driver_incentives',{}).values()),claims=list(state.get('incentive_claims',{}).values()),
                    routes=[route_plan(state,ident,True) for ident in ['unassigned']+[d['id'] for d in state['drivers'].values() if not d.get('deleted')]],
                    route_settings=state.get('route_settings',{}),attempts=list(state.get('delivery_attempts',{}).values()))
            if may_manage(user,True):
                result['workforce']['wallets']=[dict(driver_id=d['id'],name=d['name'],**wallet(state,d['id'])) for d in state['drivers'].values() if d.get('email')]


class StrictModel(BaseModel):
    model_config=ConfigDict(extra='forbid')

class Preferences(StrictModel):
    dark_mode: bool=Field(strict=True)
    notification_sound: bool=Field(strict=True)

class OvertimeDecision(StrictModel):
    approved_seconds: int = Field(ge=0, strict=True)
    note: str = Field(default='', max_length=500)


class Delegates(StrictModel):
    driver_manager: bool=Field(strict=True)
    payout_approver: bool=Field(strict=True)

class Attempt(StrictModel):
    reason: Literal['Customer unavailable','Address issue','Customer refused','Access blocked','Other']
    note: str=Field(min_length=1,max_length=1000)

class Incentive(StrictModel):
    title: str=Field(min_length=1,max_length=120)
    instructions: str=Field(min_length=1,max_length=3000)
    reward: float=Field(gt=0,le=1000000,allow_inf_nan=False)
    target_deliveries: int=Field(strict=True,ge=0,le=10000)
    driver_ids: list[str]=Field(min_length=1,max_length=200)

class Claim(StrictModel):
    note: str=Field(min_length=1,max_length=1000)
    completed: bool=Field(strict=True)

class Review(StrictModel):
    status: Literal['approved','rejected']
    note: str=Field(default='',max_length=500)

class Earning(StrictModel):
    driver_id: str=Field(min_length=1,max_length=100)
    category: Literal['base_pay','tips','peak_bonus','incentive','other','deduction']
    amount: float=Field(gt=0,le=1000000,allow_inf_nan=False)
    reference: str=Field(min_length=1,max_length=200)

class PayRule(StrictModel):
    base_pay: float=Field(ge=0,le=1000000,allow_inf_nan=False)
    peak_bonus: float=Field(ge=0,le=1000000,allow_inf_nan=False)
    peak_hours: list[int]=Field(max_length=24)

class Payout(StrictModel):
    amount: float=Field(gt=0,le=1000000,allow_inf_nan=False)
    method: Literal['cash','bank']
    bank_last4: str=Field(default='',pattern=r'^(?:|[0-9]{4})$')

class PayoutReview(StrictModel):
    status: Literal['approved','rejected','paid']
    reference: str=Field(default='',max_length=200)
    confirmed_paid: bool=Field(default=False,strict=True)

class Rating(StrictModel):
    stars: int=Field(strict=True,ge=1,le=5)
    comment: str=Field(default='',max_length=1000)

class Message(StrictModel):
    message: str=Field(default='',max_length=2000)
    attachments: list[str]=Field(default_factory=list,max_length=1)

class ChatRead(StrictModel):
    sequence: int=Field(strict=True,ge=0)

class DriverDocument(StrictModel):
    kind: Literal['Driving licence','Vehicle registration','Insurance','Pollution certificate','Permit','Other']
    filename: str=Field(min_length=1,max_length=150)
    expires_on: str=Field(pattern=r'^\d{4}-\d{2}-\d{2}$')
    data_base64: str=Field(min_length=1,max_length=2100000)

class Vehicle(StrictModel):
    plate: str=Field(min_length=1,max_length=30)
    model: str=Field(default='',max_length=100)

def idempotency(state,collection,request,user,body):
    from operations import idempotent_record
    return idempotent_record(state,collection,request,user,body)


def install_workforce(app):
    c=core()
    from operations import media_connection
    with media_connection() as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS '+('fmv_secure.' if c.DATABASE_URL else '')+
            'driver_private_files (id TEXT PRIMARY KEY, content '+('BYTEA' if c.DATABASE_URL else 'BLOB')+' NOT NULL, mime_type TEXT NOT NULL)')
    app.version='11.0.0'

    @app.patch('/users/me/preferences',tags=['Portal preferences'])
    def preferences(request:Request,body:Preferences):
        with c.transaction() as state:
            user,_=c.actor(state,request);state.setdefault('portal_preferences',{})[user['id']]=body.model_dump()
            c.audit(state,user['email'],'PREFERENCES_UPDATED');return body.model_dump()

    @app.post('/users/me/notifications/read',tags=['Portal preferences'])
    def read_notifications(request:Request):
        with c.transaction() as state:
            user,_=c.actor(state,request)
            for item in c.mine(state,'notifications',user):
                if item.get('portal','customer')==user['portal']:item['read']=True
            return {'message':'Notifications marked read.'}

    @app.post('/admin/workforce/shifts/{shift_id}/overtime', tags=['Driver management'])
    def decide_overtime(shift_id: str, request: Request, body: OvertimeDecision):
        with c.transaction() as state:
            reviewer = staff(state, request)
            shift = state['shifts'].get(shift_id, {})
            review = shift.get('overtime_review')
            c.require(shift.get('status') == 'completed' and review, 'Overtime request not found.', 404)
            c.require(reviewer['email'] != review['requested_by'], 'Another manager must review your hours.', 403)
            c.require(body.approved_seconds <= review['extra_seconds'], 'Approved time exceeds the recorded extra time.', 422)
            if review['status'] != 'pending':
                c.require(review.get('reviewer_id') == reviewer['id'] and review['approved_seconds'] == body.approved_seconds
                          and review.get('note','') == body.note.strip(), 'Another decision has already been recorded.', 409)
            else:
                review.update(status='approved' if body.approved_seconds else 'declined',
                              approved_seconds=body.approved_seconds, reviewer_id=reviewer['id'],
                              reviewed_at=c.now(), note=body.note.strip())
                c.audit(state, reviewer['email'], 'SHIFT_OVERTIME_REVIEWED', shift_id)
            return {'shift':c.shift_times(shift)}

    @app.patch('/admin/workforce/delegates/{user_id}',tags=['Driver management'])
    def delegates(user_id:str,request:Request,body:Delegates):
        with c.transaction() as state:
            manager,_=c.actor(state,request,'admin','security')
            target=next((u for u in state['users'].values() if u['id']==user_id and u['portal']=='admin'),None)
            c.require(target,'Administrator not found.',404);target.update(body.model_dump())
            c.audit(state,manager['email'],'WORKFORCE_DELEGATE_UPDATED',user_id);return {'user':c.public_user(target)}

    @app.post('/driver/deliveries/{order_id}/attempts',tags=['Delivery attempts'])
    def failed_attempt(order_id:str,request:Request,body:Attempt):
        with c.read_transaction(maintain=False) as state:limited_user,_=c.actor(state,request,'driver')
        c.rate_limit('attempt:'+limited_user['id'],12,60)
        with c.transaction() as state:
            user,_=c.actor(state,request,'driver');order=chat_access(state,user,order_id,True)
            active=next((s for s in state['shifts'].values() if s['driver_id']==user['id'] and s['status']=='active'),None)
            c.require(active,'Clock in and end your break before reporting an attempt.',409)
            existing,key,fingerprint=idempotency(state,'delivery_attempts',request,user,body)
            if existing:
                c.require(existing['order_id']==order_id,'Attempt key belongs to another order.',409);return {'attempt':existing,'message':'This failed attempt has already been reported.'}
            note=c.clean_text(body.note,1000);c.require(note,'Explain the failed attempt.',422)
            item=dict(id=c.uid('ATTEMPT'),order_id=order_id,driver_id=user['id'],reason=body.reason,note=note,
                created_at=c.now(),request_key=key,fingerprint=fingerprint)
            order.setdefault('fulfillment_started_at',item['created_at'])
            state['delivery_attempts'][item['id']]=item
            notify_managers(state,'Failed delivery attempt',order_id+': '+body.reason)
            c.notify(state,order['owner'],'Delivery attempt unsuccessful',body.reason+'. Dispatch will arrange the next step.')
            c.audit(state,user['email'],'DELIVERY_ATTEMPT_FAILED',order_id)
            return {'attempt':item,'message':'Failed attempt reported. The order remains open for dispatch or another attempt.'}

    @app.post('/admin/incentives',tags=['Driver incentives'])
    def create_incentive(request:Request,body:Incentive):
        with c.transaction() as state:
            user=staff(state,request)
            existing,key,fingerprint=idempotency(state,'incentive_publish_requests',request,user,body)
            if existing:return state['driver_incentives'][existing['campaign_id']]
            amount=c.money(body.reward);c.require(amount>0,'The incentive must be at least INR 0.01.',422)
            title=c.clean_text(body.title,120);instructions=c.clean_text(body.instructions,3000)
            c.require(title and instructions,'Title and completion instructions are required.',422)
            for ident in set(body.driver_ids):
                driver=driver_record(state,ident);c.require(driver.get('approved') and not driver.get('deleted'),'Choose approved active drivers.',409)
            item=dict(id=c.uid('INCENTIVE'),title=title,instructions=instructions,reward=amount/100,
                reward_paise=amount,driver_ids=list(set(body.driver_ids)),target_deliveries=body.target_deliveries,
                active=True,created_at=c.now(),created_by=user['email'])
            state.setdefault('driver_incentives',{})[item['id']]=item
            state['incentive_publish_requests'][key]=dict(request_key=key,fingerprint=fingerprint,campaign_id=item['id'])
            for ident in item['driver_ids']:c.notify(state,driver_record(state,ident)['email'],'New incentive instructions',title,portal='driver')
            c.audit(state,user['email'],'INCENTIVE_PUBLISHED',item['id']);return item

    @app.post('/admin/incentives/{campaign_id}/close',tags=['Driver incentives'])
    def close_incentive(campaign_id:str,request:Request):
        with c.transaction() as state:
            user=staff(state,request);item=state.get('driver_incentives',{}).get(campaign_id)
            c.require(item,'Incentive not found.',404);item['active']=False
            c.audit(state,user['email'],'INCENTIVE_CLOSED',campaign_id);return item

    @app.post('/driver/incentives/{campaign_id}/claims',tags=['Driver incentives'])
    def submit_claim(campaign_id:str,request:Request,body:Claim):
        with c.transaction() as state:
            user,_=c.actor(state,request,'driver')
            views=incentive_view(state,user['id']);campaign=next((i for i in views if i['id']==campaign_id),None)
            c.require(campaign,'Assigned incentive not found.',404)
            if campaign['claim']:return {'claim':campaign['claim'],'message':'This completion request has already been submitted.'}
            c.require(campaign['can_submit'] and body.completed,'Complete the active instructions and delivery target before submitting.',409)
            note=c.clean_text(body.note,1000);c.require(note,'Describe how the instructions were completed.',422)
            item=dict(id=c.uid('CLAIM'),campaign_id=campaign_id,driver_id=user['id'],title=campaign['title'],
                instructions=campaign['instructions'],reward_paise=campaign['reward_paise'],note=note,status='pending',created_at=c.now())
            state.setdefault('incentive_claims',{})[item['id']]=item
            notify_managers(state,'Incentive completion awaiting review',item['title'])
            c.audit(state,user['email'],'INCENTIVE_COMPLETION_SUBMITTED',item['id']);return {'claim':item,'message':'Completion sent for manager approval. Earnings are credited only after approval.'}

    @app.patch('/admin/incentive-claims/{claim_id}',tags=['Driver incentives'])
    def review_claim(claim_id:str,request:Request,body:Review):
        with c.transaction() as state:
            user=staff(state,request);claim=state.get('incentive_claims',{}).get(claim_id)
            c.require(claim,'Completion request not found.',404)
            if claim['status']==body.status:return claim
            c.require(claim['status']=='pending','This request has already been reviewed.',409)
            driver=driver_record(state,claim['driver_id'])
            c.require(user['role']=='super_admin' or user['email']!=driver['email'],'Another manager must review your completion request.',403)
            claim.update(status=body.status,reviewed_by=user['email'],reviewed_at=c.now(),review_note=c.clean_text(body.note,500))
            if body.status=='approved':add_earning(state,driver['id'],'incentive',claim['reward_paise'],claim['title'],'incentive:'+claim_id,user['email'])
            c.notify(state,driver['email'],'Incentive '+body.status,claim['title'],portal='driver')
            c.audit(state,user['email'],'INCENTIVE_'+body.status.upper(),claim_id);return claim

    @app.patch('/admin/drivers/{driver_id}/pay-rule',tags=['Driver earnings'])
    def pay_rule(driver_id:str,request:Request,body:PayRule):
        with c.transaction() as state:
            user=staff(state,request,True);driver_record(state,driver_id)
            c.require(all(type(h) is int and 0<=h<=23 for h in body.peak_hours),'Peak hours must be whole hours from 0 to 23.',422)
            rule=dict(base_pay_paise=c.money(body.base_pay),peak_bonus_paise=c.money(body.peak_bonus),peak_hours=sorted(set(body.peak_hours)),
                updated_at=c.now(),updated_by=user['email'])
            state.setdefault('driver_pay_rules',{})[driver_id]=rule;c.audit(state,user['email'],'DRIVER_PAY_RULE_UPDATED',driver_id);return rule

    @app.post('/admin/driver-earnings',tags=['Driver earnings'])
    def earning(request:Request,body:Earning):
        with c.transaction() as state:
            user=staff(state,request,True);driver_record(state,body.driver_id)
            existing,key,fingerprint=idempotency(state,'earning_requests',request,user,body)
            if existing:return state['driver_earnings'][existing['earning_id']]
            amount=c.money(body.amount);reference=c.clean_text(body.reference,200);c.require(amount>0 and reference,'A positive amount and accounts reference are required.',422)
            item=add_earning(state,body.driver_id,body.category,amount,reference,'manual:'+key,user['email'])
            state['earning_requests'][key]=dict(request_key=key,fingerprint=fingerprint,earning_id=item['id'])
            return item

    @app.post('/driver/payouts',tags=['Driver wallet'])
    def claim_payout(request:Request,body:Payout):
        with c.transaction() as state:
            user,_=c.actor(state,request,'driver');existing,key,fingerprint=idempotency(state,'driver_payouts',request,user,body)
            if existing:return existing
            amount=c.money(body.amount);c.require(0<amount<=c.money(wallet(state,user['id'])['available_to_claim']),'Amount exceeds net available earnings after COD cash and pending claims.',409)
            c.require(body.method!='bank' or bool(body.bank_last4),'Enter the last four digits of the bank account verified with accounts.',422)
            item=dict(id=c.uid('PAYOUT'),driver_id=user['id'],amount=amount/100,amount_paise=amount,method=body.method,
                bank_last4=body.bank_last4 if body.method=='bank' else '',status='pending',created_at=c.now(),request_key=key,fingerprint=fingerprint)
            state['driver_payouts'][item['id']]=item
            notify_managers(state,'Payout request awaiting review','INR %.2f · %s'%(item['amount'],item['method']),True)
            c.audit(state,user['email'],'PAYOUT_REQUESTED',item['id']);return item

    @app.patch('/admin/driver-payouts/{payout_id}',tags=['Driver wallet'])
    def payout_review(payout_id:str,request:Request,body:PayoutReview):
        with c.transaction() as state:
            user=staff(state,request,True);item=state.get('driver_payouts',{}).get(payout_id);c.require(item,'Payout not found.',404)
            if item['status']==body.status:return item
            allowed={'pending':{'approved','rejected'},'approved':{'paid','rejected'}}
            c.require(body.status in allowed.get(item['status'],set()),'Invalid payout review transition.',409)
            if body.status!='rejected':
                balance=wallet(state,item['driver_id']);other=sum(p['amount_paise'] for p in balance['requests'] if p['id']!=payout_id and p['status'] in {'pending','approved'})
                c.require(round(balance['net_earnings']*100)-other>=item['amount_paise'],'Settle outstanding COD cash or other claims before approving/paying this request.',409)
            reference=c.clean_text(body.reference,200)
            if body.status=='paid':c.require(body.confirmed_paid and reference,'Confirm actual payment and enter its receipt/transfer reference.',422)
            item.update(status=body.status,reviewed_by=user['email'],reviewed_at=c.now(),reference=reference)
            c.notify(state,driver_record(state,item['driver_id'])['email'],'Payout '+body.status,'INR %.2f · %s'%(item['amount'],item['method']),portal='driver')
            c.audit(state,user['email'],'PAYOUT_'+body.status.upper(),payout_id);return item

    @app.post('/orders/{order_id}/rating',tags=['Delivery performance'])
    def rate_order(order_id:str,request:Request,body:Rating):
        with c.transaction() as state:
            user,_=c.actor(state,request,'customer');order=chat_access(state,user,order_id)
            c.require(order['status']=='Delivered' and order.get('assigned_driver_id'),'Only a delivered order can be rated.',409)
            item=dict(order_id=order_id,driver_id=order['assigned_driver_id'],stars=body.stars,comment=c.clean_text(body.comment,1000),created_at=c.now())
            state.setdefault('delivery_ratings',{})[order_id]=item;c.audit(state,user['email'],'DELIVERY_RATED',order_id);return item

    @app.get('/orders/{order_id}/chat',tags=['Order conversations'])
    def chat_history(order_id:str,request:Request):
        with c.read_transaction() as state:
            user,_=c.actor(state,request);order=chat_access(state,user,order_id)
            raw=request.query_params.get('after','0')
            c.require(raw.isascii() and raw.isdigit() and len(raw)<=8,'Invalid conversation cursor.',422)
            messages=state.get('order_chats',{}).get(order_id,[])
            page=[m for m in messages if m['sequence']>int(raw)][:100]
            last=page[-1]['sequence'] if page else int(raw)
            return dict(messages=page,last_sequence=last,has_more=last<len(messages),
                closed=order['status'] in {'Delivered','Cancelled','Failed'})

    @app.post('/orders/{order_id}/chat',tags=['Order conversations'])
    def chat_message(order_id:str,request:Request,body:Message):
        with c.read_transaction(maintain=False) as state:
            limited_user,_=c.actor(state,request);chat_access(state,limited_user,order_id,True)
        c.rate_limit('chat:'+limited_user['id']+':'+order_id,30,60)
        with c.transaction() as state:
            user,_=c.actor(state,request);chat_access(state,user,order_id,True)
            existing,key,fingerprint=idempotency(state,'chat_send_requests',request,user,body)
            if existing:
                c.require(existing['order_id']==order_id,'Message key belongs to another order.',409);return {'message':'Message already sent.'}
            text=c.clean_text(body.message,2000);c.require(text or body.attachments,'Write a message or attach a photo / voice recording.',422)
            attachments=[]
            for ident in body.attachments:
                record=state.get('chat_attachments',{}).get(ident)
                c.require(record and record['order_id']==order_id and record['uploader_id']==user['id'], 'Attachment not found.',404)
                c.require(not record['message_id'],'Attachment has already been sent.',409)
                attachments.append(record)
            messages=state.setdefault('order_chats',{}).setdefault(order_id,[])
            c.require(len(messages)<1000,'Conversation limit reached. Contact support.',409)
            item=dict(id=c.uid('MSG'),sequence=len(messages)+1,message=text,sender_role=user['portal'],sender_name=user['full_name'],sender_id=user['id'],created_at=c.now())
            if attachments:
                from chat_media import metadata
                item['attachments']=[metadata(a) for a in attachments]
                for attachment in attachments:attachment['message_id']=item['id']
            messages.append(item);state['chat_send_requests'][key]=dict(request_key=key,fingerprint=fingerprint,order_id=order_id)
            c.audit(state,user['email'],'ORDER_MESSAGE_SENT',order_id);return item

    @app.post('/orders/{order_id}/chat/read',tags=['Order conversations'])
    def read_chat(order_id:str,request:Request,body:ChatRead|None=None):
        with c.transaction() as state:
            user,_=c.actor(state,request);chat_access(state,user,order_id)
            length=len(state.get('order_chats',{}).get(order_id,[]));sequence=body.sequence if body else length
            c.require(sequence<=length,'Invalid read position.',422)
            reads=state.setdefault('chat_reads',{});key=user['id']+':'+order_id
            reads[key]=max(reads.get(key,0),sequence)
            return {'message':'Conversation marked read.'}

    @app.patch('/driver/vehicle',tags=['Vehicle document wallet'])
    def vehicle(request:Request,body:Vehicle):
        with c.transaction() as state:
            user,_=c.actor(state,request,'driver');plate=c.clean_text(body.plate,30);c.require(plate,'Enter a vehicle plate.',422)
            item=dict(plate=plate,model=c.clean_text(body.model,100));state.setdefault('driver_vehicles',{})[user['id']]=item
            c.audit(state,user['email'],'VEHICLE_UPDATED',user['id']);return item

    @app.post('/driver/documents',tags=['Vehicle document wallet'])
    def upload_document(request:Request,body:DriverDocument):
        with c.read_transaction(maintain=False) as state:user,_=c.actor(state,request,'driver')
        c.rate_limit('driver-document:'+user['id'],10,60)
        try:date.fromisoformat(body.expires_on)
        except ValueError:raise HTTPException(422,'Enter a valid document expiration date.')
        filename=c.clean_text(body.filename,150);c.require(filename and '/' not in filename and '\\' not in filename,'Invalid document filename.',422)
        try:raw=base64.b64decode(body.data_base64,validate=True)
        except Exception:raise HTTPException(422,'Invalid document encoding.')
        c.require(0<len(raw)<=1500000,'Upload a document up to 1.5 MB.',422)
        if raw.startswith(b'%PDF-'):mime='application/pdf'
        else:
            try:
                image=Image.open(io.BytesIO(raw));c.require(image.format in {'PNG','JPEG','WEBP'} and image.width*image.height<=12000000,'Use PDF, JPEG, PNG or WebP.',422)
                image.verify();mime=Image.MIME[image.format]
            except HTTPException:raise
            except Exception:raise HTTPException(422,'Use a valid PDF, JPEG, PNG or WebP document.')
        ident=c.uid('DRVDOC')
        with media_connection() as conn:
            conn.execute(('INSERT INTO fmv_secure.driver_private_files VALUES (%s,%s,%s)' if c.DATABASE_URL else 'INSERT INTO driver_private_files VALUES (?,?,?)'),(ident,raw,mime))
        with c.transaction() as state:
            user,_=c.actor(state,request,'driver')
            c.require(len(document_view(state,user['id']))<50,'Maximum document count reached. Contact your fleet manager.',409)
            plate=state.get('driver_vehicles',{}).get(user['id'],{}).get('plate','')
            item=dict(id=ident,driver_id=user['id'],kind=body.kind,filename=filename,expires_on=body.expires_on,mime_type=mime,vehicle_plate=plate,created_at=c.now())
            if body.kind!='Other':
                for previous in state.get('driver_documents',{}).values():
                    if previous['driver_id']==user['id'] and previous['kind']==body.kind and previous.get('vehicle_plate','')==plate and not previous.get('superseded_by'):
                        previous['superseded_by']=ident
            state.setdefault('driver_documents',{})[ident]=item;c.audit(state,user['email'],'DRIVER_DOCUMENT_UPLOADED',ident);return item

    @app.get('/driver-documents/{document_id}',tags=['Vehicle document wallet'])
    def download_document(document_id:str,request:Request):
        with c.read_transaction(maintain=False) as state:
            user,_=c.actor(state,request);item=state.get('driver_documents',{}).get(document_id);c.require(item,'Document not found.',404)
            c.require((user['portal']=='driver' and user['id']==item['driver_id']) or may_manage(user),'Document not found.',404)
            filename=item['filename']
        with media_connection() as conn:
            row=conn.execute(('SELECT content,mime_type FROM fmv_secure.driver_private_files WHERE id=%s' if c.DATABASE_URL else 'SELECT content,mime_type FROM driver_private_files WHERE id=?'),(document_id,)).fetchone()
        c.require(row,'Document file not found.',404)
        from urllib.parse import quote
        return Response(bytes(row[0]),media_type=row[1],headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff',
            'Content-Disposition':"attachment; filename*=UTF-8''"+quote(filename,safe='')})

    from routing import install_routing
    install_routing(app)
    from payroll import install_payroll
    install_payroll(app)
    from chat_media import install_chat_media
    install_chat_media(app)
    app.version='12.0.0'
