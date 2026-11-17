"""Customer areas, saved addresses, published campaigns and durable mail delivery.

All records use the existing transactional application state. Campaign uploads
are public advertising assets; delivery proof and customer documents stay private.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import os
import secrets
import time
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Literal

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, EmailStr
from PIL import Image
from mail_routing import sender_for


def core():
    import secure_backend
    return secure_backend


def live(item, timestamp=None):
    stamp = time.time() if timestamp is None else timestamp
    return (item.get('status') == 'published' and not item.get('deleted')
            and (not item.get('starts_epoch') or item['starts_epoch'] <= stamp)
            and (not item.get('ends_epoch') or stamp < item['ends_epoch']))


def company_ad(state):
    candidates = sorted((c for c in state.get('campaigns', {}).values()
                         if c.get('placement') == 'email' and live(c)),
                        key=lambda c: (c['sort_order'], c['id']))
    if not candidates:
        return None
    item = candidates[0]
    asset = state.get('campaign_media', {}).get(item.get('media_id'), {})
    return dict(title=item['title'], description=item['description'],
                image_url=('https://backend.firstmilkventure.com/media/campaign/' + item['media_id'])
                if asset.get('mime_type', '').startswith('image/') else '',
                action_url='https://www.firstmilkventure.com/')


def queue_email(state, key, email, kind, **values):
    """Persist once in the same transaction as the event; never perform network I/O."""
    c = core()
    outbox = state.setdefault('mail_outbox', {})
    ident = 'MAIL-' + hashlib.sha256(key.encode()).hexdigest()[:40]
    if ident in outbox:
        return ident
    from email_templates import build_email
    rendered = build_email(kind, company_ad=company_ad(state), **values)
    outbox[ident] = dict(id=ident, to=email, kind=kind, content=rendered,
                         status='queued', attempts=0, next_attempt=time.time(),
                         lease_until=0, created_at=c.now(), last_error='')
    return ident


def queue_invitation_email(state, email, token, role):
    # The secret stays only in the private invitation email, never a list response.
    ident = queue_email(state, 'invite:' + hashlib.sha256(token.encode()).hexdigest(),
                       email, 'admin_invitation',
                       details=[('Invited email', email), ('Role', role.replace('_', ' ')),
                                ('Private invitation code', token), ('Expires', '24 hours; single use')],
                       action_url='https://admin.firstmilkventure.com/',
                       message='Open the admin portal, choose Sign up, and enter this invitation with the invited email address. Complete email verification to activate your account.')
    state['mail_outbox'][ident]['expires_epoch'] = time.time()+86400
    return ident


def queue_order_email(state, order, event):
    labels = {'placed':'Order placed', 'shipped':'Shipped',
              'out_for_delivery':'Out for delivery', 'delivered':'Delivered', 'cancelled':'Cancelled'}
    details = [('Order', order['order_number']), ('Status', labels[event]),
               ('Items', order['items_summary']),
               ('Total', 'INR %.2f' % order['total_amount']),
               ('Payment', order['payment_status'].replace('_', ' ')),
               ('Delivery date', order['delivery_date']),
               ('Delivery address', order['delivery_address'])]
    message = 'Track your order from Profile → My Orders in your First Milk account.'
    if event == 'cancelled':
        message = 'Your order has been cancelled. Reserved stock has been released. Contact support if you need assistance.'
    if event == 'out_for_delivery':
        details.append(('Delivery PIN', order['delivery_otp']))
        message += ' Share your delivery PIN only when you receive the order.'
    return queue_email(state, 'order:' + order['id'] + ':' + event, order['owner'],
                       'order_' + event, name=order['customer_name'], details=details,
                       message=message)


def send_rendered_mail(item):
    c = core()
    api_key = os.getenv('RESEND_API_KEY', '')
    c.require(bool(api_key), 'Email delivery is not configured.', 503)
    content = item['content']
    try:
        response = httpx.post('https://api.resend.com/emails',
            headers={'Authorization':'Bearer ' + api_key, 'Idempotency-Key':item['id']},
            json={'from':sender_for(item['kind']),
                  'to':[item['to']], **content}, timeout=15)
        c.require(response.is_success, 'Mail provider rejected the message (%s).' % response.status_code, 503)
        return str(response.json().get('id', ''))
    except (httpx.HTTPError, ValueError):
        raise HTTPException(503, 'Mail provider could not confirm delivery.')


def drain_mail_outbox(limit=20):
    """Lease messages, send after commit, retry failures without rolling back orders."""
    c = core()
    for _ in range(limit):
        with c.transaction() as state:
            stamp = time.time()
            item = next((m for m in state.get('mail_outbox', {}).values()
                         if m['status'] in {'queued','retry','sending'}
                         and m['next_attempt'] <= stamp and m.get('lease_until',0) <= stamp), None)
            if item is None:
                return
            if item.get('expires_epoch') and stamp >= item['expires_epoch']:
                item.update(status='expired', content={}, last_error='Invitation expired before email delivery. Create a new invitation.')
                continue
            # Resend retains idempotency keys for 24h. Stop automatic retries
            # within that window so a long outage cannot cause a duplicate send.
            if item['attempts'] and stamp - item.get('first_attempt', stamp) >= 23 * 3600:
                item.update(status='needs_review', last_error='Retry window ended; review provider delivery before retrying.')
                continue
            lease = secrets.token_hex(12)
            item.update(status='sending', lease=lease, lease_until=stamp+90,
                        attempts=item['attempts']+1, first_attempt=item.get('first_attempt', stamp))
            payload = dict(item)
        try:
            provider_id = send_rendered_mail(payload)
            error = ''
        except Exception as exc:
            provider_id = ''
            # Do not retain provider response bodies, credentials or message content in logs.
            error = str(exc.detail) if isinstance(exc, HTTPException) else 'Mail delivery failed. Retrying.'
        with c.transaction() as state:
            current = state['mail_outbox'].get(payload['id'])
            if not current or current.get('lease') != lease:
                continue
            current['lease_until'] = 0
            if not error:
                current.update(status='sent', sent_at=c.now(), provider_id=provider_id, last_error='')
                # Invitation secret is no longer needed once the provider accepts it.
                if current['kind'] == 'admin_invitation':
                    current['content'] = {}
            else:
                current.update(status='retry', last_error=error,
                               next_attempt=time.time()+min(3600,30 * (2 ** min(current['attempts'],7))))


class Address(BaseModel):
    model_config = ConfigDict(extra='forbid')
    label: str = Field(min_length=1, max_length=40)
    recipient: str = Field(min_length=1, max_length=150)
    phone: str = Field(min_length=10, max_length=25)
    address: str = Field(min_length=1, max_length=1000)
    is_default: bool = False


def addresses_for(state, user):
    records = state.setdefault('addresses', {})
    mine = [a for a in records.values() if a['owner'] == user['email']]
    if not mine and user.get('address'):
        ident = core().uid('ADDR')
        entry = dict(id=ident,owner=user['email'],label='Home',recipient=user['full_name'],
                     phone=user['phone'],address=user['address'],is_default=True)
        records[ident] = entry
        mine = [entry]
    return mine


def resolve_checkout_address(state, user, body):
    c = core()
    if body.address_id:
        item = state.setdefault('addresses', {}).get(body.address_id)
        c.require(item and item['owner'] == user['email'], 'Saved address not found.', 404)
        return c.clean_text(item['address'],1000)
    return c.clean_text(body.delivery_address or user['address'],1000)


class Campaign(BaseModel):
    model_config = ConfigDict(extra='forbid')
    title: str = Field(min_length=1,max_length=120)
    description: str = Field(default='',max_length=1500)
    kind: Literal['banner','offer','menu','post'] = 'banner'
    placement: Literal['home','offers','email'] = 'home'
    status: Literal['draft','published'] = 'published'
    media_id: str = Field(default='',max_length=100)
    alt_text: str = Field(default='',max_length=200)
    animation: Literal['none','fade','slide'] = 'fade'
    cta_label: str = Field(default='Explore',max_length=40)
    cta_path: Literal['home','catalog','offers','profile'] = 'catalog'
    terms: str = Field(default='',max_length=1000)
    starts_at: str = Field(default='',max_length=40)
    ends_at: str = Field(default='',max_length=40)
    sort_order: int = Field(default=0,ge=0,le=10000)
    version: int | None = Field(default=None,ge=1)


class CampaignMedia(BaseModel):
    filename: str = Field(min_length=1,max_length=200)
    data_base64: str = Field(max_length=2100000)


class Invitation(BaseModel):
    email: EmailStr
    role: Literal['super_admin','operations_manager','finance','customer_support','content_manager']


class ContentPermission(BaseModel):
    enabled: bool


class OrderStatus(BaseModel):
    status: Literal['Shipped','Out for Delivery']


class CompanyPage(BaseModel):
    title: str = Field(min_length=1,max_length=120)
    body: str = Field(min_length=1,max_length=20000)


def content_actor(state, request):
    c = core()
    user, _ = c.actor(state,request,'admin')
    c.require(user['role'] in {'super_admin','content_manager'} or user.get('content_editor') is True,
              'Only a super admin or designated content editor can manage ads.',403)
    return user


def campaign_record(state, body, actor_email, old=None):
    c = core()
    data = body.model_dump(exclude={'version'})
    data['title'] = c.clean_text(data['title'],120)
    c.require(data['title'], 'A title is required.',422)
    for field in ('description','alt_text','terms','cta_label'):
        data[field] = c.clean_text(data[field],1500)
    def epoch(value):
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace('Z','+00:00'))
            c.require(parsed.tzinfo is not None,'Schedule must include a timezone.',422)
            return parsed.timestamp()
        except ValueError:
            raise HTTPException(422,'Invalid campaign schedule.')
    data['starts_epoch'] = epoch(data['starts_at'])
    data['ends_epoch'] = epoch(data['ends_at'])
    c.require(not data['ends_epoch'] or not data['starts_epoch'] or data['ends_epoch'] > data['starts_epoch'],
              'End time must follow start time.',422)
    if data['media_id']:
        c.require(data['media_id'] in state.setdefault('campaign_media',{}),'Upload not found.',404)
    return dict(data,id=old['id'] if old else c.uid('AD'),version=(old['version']+1) if old else 1,
                created_at=old['created_at'] if old else c.now(),updated_at=c.now(),updated_by=actor_email)


def public_campaign(state, item):
    asset = state.get('campaign_media',{}).get(item['media_id'],{})
    return {k:v for k,v in dict(item,media_url='/media/campaign/'+item['media_id'] if asset else '',
                               mime_type=asset.get('mime_type','')).items() if k not in {'updated_by'}}


def install_experience(app):
    c = core()
    app.version = '9.0.0'

    @app.get('/users/me/addresses',tags=['Saved addresses'])
    def list_addresses(request:Request):
        with c.transaction() as state:
            user,_ = c.actor(state,request,'customer')
            return {'addresses':addresses_for(state,user),'user':c.public_user(user)}

    @app.post('/users/me/addresses',tags=['Saved addresses'])
    def add_address(request:Request,body:Address):
        with c.transaction() as state:
            user,_ = c.actor(state,request,'customer')
            owned = addresses_for(state,user)
            c.require(len(owned)<20,'You can save up to 20 addresses.',409)
            data = body.model_dump()
            data.update(address=c.clean_text(body.address,1000),label=c.clean_text(body.label,40),
                        recipient=c.clean_text(body.recipient,150),phone=c.phone_india(body.phone))
            c.require(all(data[k] for k in ('address','label','recipient')),'Complete all address fields.',422)
            data.update(id=c.uid('ADDR'),owner=user['email'],is_default=body.is_default or not owned)
            if data['is_default']:
                for old in owned: old['is_default']=False
                user['address']=data['address']
            state['addresses'][data['id']]=data
            c.audit(state,user['email'],'ADDRESS_CREATED',data['id'])
            return {'address':data}

    @app.patch('/users/me/addresses/{address_id}',tags=['Saved addresses'])
    def update_address(address_id:str,request:Request,body:Address):
        with c.transaction() as state:
            user,_=c.actor(state,request,'customer')
            item=state.setdefault('addresses',{}).get(address_id)
            c.require(item and item['owner']==user['email'],'Saved address not found.',404)
            data=body.model_dump()
            data.update(address=c.clean_text(body.address,1000),label=c.clean_text(body.label,40),
                        recipient=c.clean_text(body.recipient,150),phone=c.phone_india(body.phone))
            c.require(all(data[k] for k in ('address','label','recipient')),'Complete all address fields.',422)
            # A current default remains default until another address is selected.
            data['is_default']=body.is_default or item['is_default']
            item.update(data)
            if item['is_default']:
                for old in addresses_for(state,user):old['is_default']=old['id']==address_id
                user['address']=item['address']
            c.audit(state,user['email'],'ADDRESS_UPDATED',address_id)
            return {'address':item}

    @app.delete('/users/me/addresses/{address_id}',tags=['Saved addresses'])
    def delete_address(address_id:str,request:Request):
        with c.transaction() as state:
            user,_=c.actor(state,request,'customer')
            item=state.setdefault('addresses',{}).get(address_id)
            c.require(item and item['owner']==user['email'],'Saved address not found.',404)
            del state['addresses'][address_id]
            remaining=[a for a in state['addresses'].values() if a['owner']==user['email']]
            if item['is_default']:
                for index,old in enumerate(remaining):old['is_default']=index==0
                user['address']=remaining[0]['address'] if remaining else ''
            c.audit(state,user['email'],'ADDRESS_DELETED',address_id)
            return {'message':'Address removed. Existing orders keep their delivery address.'}

    @app.get('/content',tags=['Published campaigns'])
    def published_content(request:Request):
        with c.read_transaction() as state:
            c.actor(state,request)
            return {'campaigns':[public_campaign(state,x) for x in sorted(state.get('campaigns',{}).values(),
                                  key=lambda x:(x['sort_order'],x['id'])) if live(x) and x['placement']!='email']}

    @app.get('/pages/{slug}',tags=['Company information'])
    def company_page(slug:str,request:Request):
        c.require(slug in {'terms','privacy','refund','about','contact'},'Page not found.',404)
        with c.read_transaction() as state:
            c.actor(state,request)
            return {'page':state.get('company_pages',{}).get(slug)}

    @app.get('/admin/pages',tags=['Company information'])
    def list_pages(request:Request):
        with c.read_transaction() as state:
            content_actor(state,request)
            return {'pages':state.get('company_pages',{})}

    @app.patch('/admin/pages/{slug}',tags=['Company information'])
    def save_page(slug:str,request:Request,body:CompanyPage):
        c.require(slug in {'terms','privacy','refund','about','contact'},'Page not found.',404)
        with c.transaction() as state:
            user=content_actor(state,request)
            title=c.clean_text(body.title,120);text=c.clean_text(body.body,20000)
            c.require(title and text,'Complete the title and page content.',422)
            item=dict(title=title,body=text,updated_at=c.now())
            state.setdefault('company_pages',{})[slug]=item
            c.audit(state,user['email'],'COMPANY_PAGE_UPDATED',slug)
            return {'page':item}

    @app.get('/admin/content',tags=['Campaign management'])
    def list_campaigns(request:Request):
        with c.read_transaction() as state:
            content_actor(state,request)
            return {'campaigns':[public_campaign(state,x) for x in state.get('campaigns',{}).values() if not x.get('deleted')]}

    @app.post('/admin/content',tags=['Campaign management'])
    def create_campaign(request:Request,body:Campaign):
        with c.transaction() as state:
            user=content_actor(state,request)
            item=campaign_record(state,body,user['email'])
            state.setdefault('campaigns',{})[item['id']]=item
            c.audit(state,user['email'],'CAMPAIGN_CREATED',item['id'])
            return {'campaign':public_campaign(state,item)}

    @app.patch('/admin/content/{campaign_id}',tags=['Campaign management'])
    def edit_campaign(campaign_id:str,request:Request,body:Campaign):
        with c.transaction() as state:
            user=content_actor(state,request)
            old=state.setdefault('campaigns',{}).get(campaign_id)
            c.require(old and not old.get('deleted'),'Campaign not found.',404)
            c.require(body.version==old['version'],'Campaign changed. Refresh before saving.',409)
            item=campaign_record(state,body,user['email'],old)
            state['campaigns'][campaign_id]=item
            c.audit(state,user['email'],'CAMPAIGN_UPDATED',campaign_id)
            return {'campaign':public_campaign(state,item)}

    @app.delete('/admin/content/{campaign_id}',tags=['Campaign management'])
    def delete_campaign(campaign_id:str,request:Request):
        with c.transaction() as state:
            user=content_actor(state,request)
            item=state.setdefault('campaigns',{}).get(campaign_id)
            c.require(item and not item.get('deleted'),'Campaign not found.',404)
            item['deleted']=True
            c.audit(state,user['email'],'CAMPAIGN_DELETED',campaign_id)
            return {'message':'Campaign removed from publishing.'}

    @app.post('/admin/content-media',tags=['Campaign management'])
    def upload_campaign_media(request:Request,body:CampaignMedia):
        with c.transaction() as state:
            user=content_actor(state,request)
            try:raw=base64.b64decode(body.data_base64,validate=True)
            except (ValueError,TypeError):raise HTTPException(422,'Invalid upload.')
            c.require(0<len(raw)<=1500000,'Upload a file up to 1.5 MB.',422)
            if len(raw)>12 and raw[4:8]==b'ftyp' and raw[8:12] in {b'isom',b'iso2',b'mp41',b'mp42',b'avc1',b'M4V '}:
                mime='video/mp4'
            else:
                try:
                    image=Image.open(io.BytesIO(raw))
                    c.require(image.format in {'JPEG','PNG','GIF','WEBP'} and max(image.size)<=4096
                              and image.width*image.height<=12000000 and getattr(image,'n_frames',1)<=300,
                              'Use PNG, JPEG, GIF or WebP, up to 4096 pixels and 300 frames.',422)
                    mime=Image.MIME[image.format]
                    image.verify()
                except HTTPException:raise
                except Exception:raise HTTPException(422,'Invalid image. Use PNG, JPEG, GIF, WebP or MP4.')
            ident=c.uid('MEDIA')
            state.setdefault('campaign_media',{})[ident]=dict(id=ident,mime_type=mime,
                data_base64=base64.b64encode(raw).decode(),created_at=c.now())
            c.audit(state,user['email'],'CAMPAIGN_MEDIA_UPLOADED',ident)
            return {'id':ident,'mime_type':mime,'url':'/media/campaign/'+ident}

    @app.get('/media/campaign/{media_id}',tags=['Public advertising assets'])
    def campaign_asset(media_id:str):
        with c.read_transaction() as state:
            item=state.get('campaign_media',{}).get(media_id)
            c.require(item,'Asset not found.',404)
            return Response(base64.b64decode(item['data_base64']),media_type=item['mime_type'],
                            headers={'Cache-Control':'public, max-age=3600','X-Content-Type-Options':'nosniff'})

    # Replace the legacy prompt-oriented route while retaining its original schema.
    app.router.routes[:]=[r for r in app.routes if not (getattr(r,'path','')=='/admin/invitations' and 'POST' in getattr(r,'methods',set()))]

    @app.post('/admin/invitations',tags=['Staff invitations'])
    def create_invitation(request:Request,body:Invitation):
        with c.transaction() as state:
            user,_=c.actor(state,request,'admin','security')
            email=str(body.email).lower()
            c.require('admin:'+email not in state['users'],'This administrator already has an account.',409)
            for digest,old in state['invites'].items():
                if old['email']==email and not old.get('used'):
                    old['used']=True
                    mail_id='MAIL-'+hashlib.sha256(('invite:'+digest).encode()).hexdigest()[:40]
                    pending=state.get('mail_outbox',{}).get(mail_id)
                    if pending and pending['status'] in {'queued','retry'}:
                        pending.update(status='replaced',content={},last_error='Replaced by a newer invitation.')
            token=secrets.token_urlsafe(32)
            state['invites'][hashlib.sha256(token.encode()).hexdigest()]={'email':email,'role':body.role,
                'expires':time.time()+86400,'used':False,'created_by':user['email']}
            queue_invitation_email(state,email,token,body.role)
            c.audit(state,user['email'],'STAFF_INVITED',email)
            return {'invitation':token,'message':'Invitation created. Email queued; you can also copy this private code.','expires_in':86400}

    @app.get('/admin/invitations',tags=['Staff invitations'])
    def list_invitations(request:Request):
        with c.read_transaction() as state:
            c.actor(state,request,'admin','security')
            return {'invitations':[dict(email=i['email'],role=i['role'],expires_at=i['expires'],
                  status='used or replaced' if i.get('used') else 'expired' if i['expires']<=time.time() else 'pending')
                  for i in state['invites'].values()],
                  'staff':[c.public_user(u) for u in state['users'].values() if u['portal']=='admin']}

    @app.patch('/admin/content-editors/{user_id}',tags=['Campaign management'])
    def designate_editor(user_id:str,request:Request,body:ContentPermission):
        with c.transaction() as state:
            actor,_=c.actor(state,request,'admin','security')
            user=next((u for u in state['users'].values() if u['id']==user_id and u['portal']=='admin'),None)
            c.require(user,'Administrator not found.',404)
            user['content_editor']=body.enabled
            c.audit(state,actor['email'],'CONTENT_EDITOR_CHANGED',user_id)
            return {'user':c.public_user(user)}

    @app.get('/admin/mail-outbox',tags=['Mail delivery'])
    def mail_delivery_status(request:Request):
        with c.read_transaction() as state:
            c.actor(state,request,'admin','security')
            return {'messages':[{k:v for k,v in m.items() if k in {'id','to','kind','status','attempts','created_at','sent_at','last_error','next_attempt'}}
                                for m in list(state.get('mail_outbox',{}).values())[-200:]]}

    @app.patch('/admin/deliveries/{delivery_id}/status',tags=['Dispatch updates'])
    def update_dispatch(delivery_id:str,request:Request,body:OrderStatus):
        with c.transaction() as state:
            user,_=c.actor(state,request,'admin','orders')
            order=state['orders'].get(delivery_id)
            c.require(order,'Order not found.',404)
            allowed={'Shipped':{'Assigned','Picked Up'},'Out for Delivery':{'Assigned','Picked Up','In Transit'}}
            c.require(order['status'] in allowed[body.status] and order.get('assigned_driver_id'),
                      'Assign an approved driver before dispatch; closed orders cannot be changed.',409)
            order['status']='Picked Up' if body.status=='Shipped' else 'In Transit'
            queue_order_email(state,order,'shipped')
            if body.status=='Out for Delivery':queue_order_email(state,order,'out_for_delivery')
            c.notify(state,order['owner'],'Delivery updated',body.status)
            c.audit(state,user['email'],'ORDER_DISPATCH_UPDATED',delivery_id)
            return {'order':c.order_view(order),'message':body.status+'. Customer email queued.'}

    # Existing installation uses one JSON/JSONB state row, so setdefault is the
    # additive migration for both SQLite and PostgreSQL installations.
    with c.transaction() as state:
        for key in ('addresses','campaigns','campaign_media','mail_outbox'):state.setdefault(key,{})

    @app.middleware('http')
    async def drain_after_commit(request,call_next):
        response=await call_next(request)
        if request.method in {'POST','PATCH','DELETE'} and response.status_code<400 and os.getenv('APP_ENV')!='test':
            from starlette.background import BackgroundTask, BackgroundTasks
            previous=response.background
            response.background=BackgroundTasks()
            if previous:response.background.add_task(previous)
            response.background.add_task(drain_mail_outbox)
        return response

    previous_lifespan=app.router.lifespan_context
    @asynccontextmanager
    async def lifespan(application):
        async with previous_lifespan(application) as state:
            async def mail_worker():
                while True:
                    try:
                        def maintain_and_send():
                            with c.transaction() as state:c.expire_reservations(state)
                            drain_mail_outbox()
                        await asyncio.to_thread(maintain_and_send)
                    except Exception:pass  # Records remain queued for the next attempt.
                    await asyncio.sleep(30)
            task=asyncio.create_task(mail_worker()) if os.getenv('APP_ENV')!='test' else None
            try:yield state
            finally:
                if task:
                    task.cancel()
                    try:await task
                    except asyncio.CancelledError:pass
    app.router.lifespan_context=lifespan

