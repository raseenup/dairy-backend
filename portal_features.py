"""Additive, permission-checked company assistant, payment and website tools.

All writes use the existing transactional state; no client-side secret or paid
provider is necessary to review/publish content. Live AI requires OPENAI_API_KEY.
"""
from __future__ import annotations

import os
import re
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

import httpx
from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

import secure_backend as c

COPY_KEYS = {
    'topline','hero_title','hero_description','products_title','products_description',
    'why_title','why_description','how_title','how_description','story_title',
    'story_description','contact_title','contact_description','footer_note',
    'careers_title','careers_description'
}
DEFAULT_SITE = {
    'copy':{}, 'blocks':[], 'media_id':'', 'media_fit':'cover',
    'background_color':'#103f36','version':1
}
UPI_PATTERN = re.compile(r'^[A-Za-z0-9._-]{2,80}@[A-Za-z0-9.-]{2,60}$')
WORDS = re.compile(r'[a-z0-9]{3,}')
STOP_WORDS = {'the','and','for','can','you','what','how','are','our','your','from',
              'this','that','with','about','when','where','please','will'}

class AssistantQuestion(BaseModel):
    model_config = ConfigDict(extra='forbid')
    message: str = Field(min_length=2,max_length=1200)

class KnowledgeBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    title: str = Field(min_length=3,max_length=120)
    body: str = Field(min_length=5,max_length=8000)
    status: str = Field(default='draft',pattern=r'^(draft|published)$')
    version: int | None = Field(default=None,ge=1)

class SiteBlock(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(min_length=1,max_length=64)
    page: str = Field(pattern=r'^(home|careers)$')
    placement: str = Field(pattern=r'^(after-hero|after-products|after-why|before-footer)$')
    title: str = Field(min_length=2,max_length=160)
    body: str = Field(default='',max_length=1400)
    media_id: str = Field(default='',max_length=100)
    variant: str = Field(default='feature',pattern=r'^(feature|banner|quote|cta)$')

class SiteBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    copy: dict[str,str] = Field(default_factory=dict)
    blocks: list[SiteBlock] = Field(default_factory=list,max_length=24)
    media_id: str = Field(default='',max_length=100)
    media_fit: str = Field(default='cover',pattern=r'^(cover|contain)$')
    background_color: str = Field(default='#103f36',pattern=r'^#[A-Fa-f0-9]{6}$')
    version: int = Field(ge=1)

class UPISettings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    upi_id: str = Field(min_length=5,max_length=145)
    payee_name: str = Field(min_length=2,max_length=100)
    enabled: bool = False

class TransferRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    amount: str = Field(min_length=1,max_length=24)
    note: str = Field(default='Customer payment',max_length=120)

class TransferReported(BaseModel):
    model_config = ConfigDict(extra='forbid')
    transaction_reference: str = Field(min_length=8,max_length=64)

class BankConfirmation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    bank_reference: str = Field(min_length=8,max_length=64)
    verified_amount: str = Field(min_length=1,max_length=24)

class ReturnRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    order_id: str = Field(min_length=1,max_length=100)
    reason: str = Field(min_length=8,max_length=1500)
    request_type: str = Field(default='return',pattern=r'^(return|refund)$')

class ReturnDecision(BaseModel):
    model_config = ConfigDict(extra='forbid')
    status: str = Field(pattern=r'^(approved|rejected|received)$')

class RefundConfirmation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    method: str = Field(pattern=r'^(razorpay|manual)$')
    refund_reference: str = Field(min_length=6,max_length=120)
    amount: str = Field(min_length=1,max_length=24)

def clean_amount(value):
    try:
        parsed = Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        raise HTTPException(422,'Enter a valid amount in rupees.')
    c.require(parsed.is_finite() and parsed>0 and parsed.as_tuple().exponent>=-2
              and len(parsed.as_tuple().digits)<=16,
              'Enter a positive INR amount with up to two decimal places.',422)
    return parsed

def editing_actor(state,request):
    user,_=c.actor(state,request,'admin')
    c.require(user['role'] in {'super_admin','content_manager'}
              or user.get('content_editor') is True,
              'Only an administrator or designated content editor can publish.',403)
    return user

def super_admin(state,request):
    user,_=c.actor(state,request,'admin')
    c.require(user['role']=='super_admin','Only the super administrator may change receiving UPI details.',403)
    return user

def company_info(state):
    pages=state.get('company_pages',{})
    entries=[
        {'id':'page:'+slug,'title':page['title'],'body':page['body'],'updated_at':page.get('updated_at')}
        for slug,page in pages.items()
        if slug in {'terms','privacy','refund','about','contact'} and page.get('body')
    ]
    entries.extend({'id':item['id'],'title':item['title'],'body':item['body'],
                    'updated_at':item.get('updated_at')}
                   for item in state.get('assistant_knowledge',{}).values()
                   if item.get('status')=='published')
    return entries

def answer_from_content(message,entries):
    query=set(WORDS.findall(message.lower()))-STOP_WORDS
    ranking=sorted(
        entries,
        key=lambda item: len(query & (set(WORDS.findall(item['title'].lower()))-STOP_WORDS))*5
              + len(query & (set(WORDS.findall(item['body'].lower()))-STOP_WORDS)),
        reverse=True)
    matches=[item for item in ranking if query and
             query & set(WORDS.findall((item['title']+' '+item['body']).lower()))][:3]
    return matches

def assistant_answer(message,entries):
    matches=answer_from_content(message,entries)
    if not matches:
        return {'reply':"I can't confirm that from the company's published information. Please contact our support team for an accurate answer.",
                'mode':'published_knowledge','sources':[]}
    selected=[{'id':item['id'],'title':item['title']} for item in matches]
    key=os.getenv('OPENAI_API_KEY','')
    if key:
        context='\n\n'.join(item['title']+'\n'+item['body'][:5500] for item in matches)
        try:
            response=httpx.post('https://api.openai.com/v1/responses',
                headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'},
                json={'model':os.getenv('FMV_ASSISTANT_MODEL','gpt-4.1-mini'),
                      'instructions':('You answer for First Milk Venture. Use ONLY the supplied '
                        'approved company facts. Do not invent policies, refund approvals, '
                        'payment confirmations, guarantees or personal account data. '
                        'If the answer is not established, say support must confirm.'),
                      'input':'Published facts:\n'+context+'\n\nCustomer question:\n'+message,
                      'max_output_tokens':350},timeout=12)
            response.raise_for_status()
            payload=response.json()
            parts=[content.get('text','') for item in payload.get('output',[])
                   for content in item.get('content',[]) if content.get('type')=='output_text']
            reply='\n'.join(parts).strip()
            if reply:return {'reply':reply,'mode':'ai_grounded','sources':selected}
        except (httpx.HTTPError,ValueError,KeyError,TypeError):
            pass
    excerpt=matches[0]['body'].strip()
    return {'reply':matches[0]['title']+': '+excerpt[:650]+('…' if len(excerpt)>650 else ''),
            'mode':'published_knowledge','sources':selected}

def site_view(state):
    site={**DEFAULT_SITE,**state.get('website_design',{})}
    media=state.get('campaign_media',{}).get(site.get('media_id',''))
    public_blocks=[]
    for block in site.get('blocks',[]):
        item=dict(block)
        meta=state.get('campaign_media',{}).get(item.get('media_id',''))
        item['media_url']='/media/campaign/'+meta['id'] if meta else ''
        item['media_type']=meta.get('mime_type','') if meta else ''
        item.pop('media_id',None)
        public_blocks.append(item)
    return {'copy':site['copy'],'blocks':public_blocks,'media_url':'/media/campaign/'+media['id'] if media else '',
            'media_type':media.get('mime_type','') if media else '',
            'media_fit':site['media_fit'],'background_color':site['background_color'],
            'version':site['version']}

def upi_view(state):
    cfg=state.get('merchant_upi',{})
    return {'enabled':bool(cfg.get('enabled') and cfg.get('upi_id')),
            'upi_id':cfg.get('upi_id','') if cfg.get('enabled') else '',
            'payee_name':cfg.get('payee_name','') if cfg.get('enabled') else '',
            'currency':'INR'}

def install_portal_features(app):
    with c.transaction() as state:
        for key in ('assistant_knowledge','website_design','merchant_upi','upi_transfers'):
            state.setdefault(key,{})

    @app.post('/assistant/chat',tags=['Approved information chatbot'])
    def chat(request:Request,body:AssistantQuestion):
        with c.read_transaction() as state:
            user,_=c.actor(state,request)
            docs=company_info(state)
        c.rate_limit('assistant:'+user['id'],30,60)
        return assistant_answer(c.clean_text(body.message,1200),docs)

    @app.post('/public/assistant/chat',tags=['Public published-information chatbot'])
    def public_chat(request:Request,body:AssistantQuestion):
        c.rate_limit('public-assistant:'+str(request.client.host if request.client else 'unknown'),12,60)
        with c.read_transaction() as state:
            docs=company_info(state)
        return assistant_answer(c.clean_text(body.message,1200),docs)

    @app.get('/admin/assistant/knowledge',tags=['Assistant editor'])
    def read_knowledge(request:Request):
        with c.read_transaction() as state:
            editing_actor(state,request)
            return {'items':list(state['assistant_knowledge'].values()),
                    'company_pages':state.get('company_pages',{})}

    @app.post('/admin/assistant/knowledge',tags=['Assistant editor'])
    def create_knowledge(request:Request,body:KnowledgeBody):
        with c.transaction() as state:
            user=editing_actor(state,request);entry=body.model_dump(exclude={'version'})
            entry.update(id=c.uid('KB'),version=1,updated_at=c.now())
            state['assistant_knowledge'][entry['id']]=entry
            c.audit(state,user['email'],'ASSISTANT_KNOWLEDGE_CREATED',entry['id'])
            return {'item':entry}

    @app.patch('/admin/assistant/knowledge/{knowledge_id}',tags=['Assistant editor'])
    def update_knowledge(knowledge_id:str,request:Request,body:KnowledgeBody):
        with c.transaction() as state:
            user=editing_actor(state,request)
            item=state['assistant_knowledge'].get(knowledge_id)
            c.require(item,'Knowledge entry not found.',404)
            c.require(body.version==item['version'],'This article changed. Refresh before saving.',409)
            item.update(body.model_dump(exclude={'version'}))
            item.update(version=item['version']+1,updated_at=c.now())
            c.audit(state,user['email'],'ASSISTANT_KNOWLEDGE_UPDATED',knowledge_id)
            return {'item':item}

    @app.get('/public/website',tags=['Published website design'])
    def published_website():
        with c.read_transaction() as state:return site_view(state)

    @app.get('/admin/website',tags=['Website editor'])
    def admin_website(request:Request):
        with c.read_transaction() as state:
            editing_actor(state,request)
            return {**site_view(state), 'media_id':state.get('website_design',{}).get('media_id',''),
                    'blocks':state.get('website_design',{}).get('blocks',[])}

    @app.patch('/admin/website',tags=['Website editor'])
    def update_website(request:Request,body:SiteBody):
        with c.transaction() as state:
            user=editing_actor(state,request)
            old={**DEFAULT_SITE,**state.get('website_design',{})}
            c.require(body.version==old['version'],'Website changed. Reload before publishing.',409)
            c.require(set(body.copy)<=COPY_KEYS,'Invalid website field.',422)
            copy={key:c.clean_text(value,1200) for key,value in body.copy.items()}
            if body.media_id:
                c.require(body.media_id in state['campaign_media'],'Upload a valid image, GIF or video first.',422)
            blocks=[block.model_dump() for block in body.blocks]
            c.require(len({block['id'] for block in blocks})==len(blocks),'Each section needs a unique ID.',422)
            for block in blocks:
                if block['media_id']:
                    c.require(block['media_id'] in state['campaign_media'],'Upload section media first.',422)
                block['title']=c.clean_text(block['title'],160)
                block['body']=c.clean_text(block['body'],1400)
            updated={'copy':copy,'blocks':blocks,'media_id':body.media_id,'media_fit':body.media_fit,
                     'background_color':body.background_color,'version':old['version']+1}
            state['website_design']=updated
            c.audit(state,user['email'],'WEBSITE_DESIGN_PUBLISHED')
            return site_view(state)

    @app.get('/admin/payment/upi',tags=['UPI settings'])
    def get_upi_admin(request:Request):
        with c.read_transaction() as state:
            super_admin(state,request)
            return {'settings':state['merchant_upi'],'transfers':list(state['upi_transfers'].values())}

    @app.patch('/admin/payment/upi',tags=['UPI settings'])
    def set_upi(request:Request,body:UPISettings):
        with c.transaction() as state:
            user=super_admin(state,request)
            vpa=body.upi_id.strip()
            c.require(bool(UPI_PATTERN.fullmatch(vpa)),'Enter a valid merchant UPI ID.',422)
            state['merchant_upi']={'upi_id':vpa,'payee_name':c.clean_text(body.payee_name,100),
                'enabled':body.enabled,'updated_at':c.now()}
            c.audit(state,user['email'],'MERCHANT_UPI_CHANGED')
            return {'settings':state['merchant_upi']}

    @app.get('/customer/payment/upi',tags=['Customer UPI'])
    def get_customer_upi(request:Request):
        with c.read_transaction() as state:
            c.actor(state,request,'customer')
            return {'settings':upi_view(state),
                    'transfers':[x for x in state['upi_transfers'].values() if x['owner']==
                        c.actor(state,request,'customer')[0]['email']]}

    @app.post('/customer/payment/upi',tags=['Customer UPI'])
    def create_upi_intent(request:Request,body:TransferRequest):
        amount=clean_amount(body.amount)
        c.rate_limit('upi-intent:'+str(request.client.host if request.client else 'unknown'),15,60)
        with c.transaction() as state:
            user,_=c.actor(state,request,'customer')
            cfg=upi_view(state)
            c.require(cfg['enabled'],'Merchant UPI payments are not enabled.',503)
            ident=c.uid('UPI')
            payment={'id':ident,'owner':user['email'],'amount':str(amount),
                     'note':c.clean_text(body.note,120),'status':'initiated',
                     'created_at':c.now(),'payment_rail':'manual_upi'}
            state['upi_transfers'][ident]=payment
            c.audit(state,user['email'],'UPI_INTENT_CREATED',ident)
            query=urlencode({'pa':cfg['upi_id'],'pn':cfg['payee_name'],'am':str(amount),
                             'cu':'INR','tr':ident,'tn':'First Milk Venture '+ident})
            return {'transfer':payment,'upi_url':'upi://pay?'+query,
                    'message':'Open a UPI app. Payments are pending until bank receipt is verified.'}

    @app.post('/customer/payment/upi/{transfer_id}/reported',tags=['Customer UPI'])
    def report_upi(transfer_id:str,request:Request,body:TransferReported):
        with c.transaction() as state:
            user,_=c.actor(state,request,'customer')
            payment=c.owned(state,'upi_transfers',transfer_id,user)
            c.require(payment['status'] in {'initiated','reported'},'Transfer already reviewed.',409)
            payment['customer_reference']=c.clean_text(body.transaction_reference,64)
            payment['status']='reported'
            c.audit(state,user['email'],'UPI_CUSTOMER_REPORTED',transfer_id)
            return {'transfer':payment,'message':'Reference received. Bank reconciliation is pending.'}

    @app.post('/admin/payment/upi/{transfer_id}/confirm',tags=['UPI reconciliation'])
    def verify_bank_receipt(transfer_id:str,request:Request,body:BankConfirmation):
        with c.transaction() as state:
            user=super_admin(state,request)
            payment=state['upi_transfers'].get(transfer_id)
            c.require(payment and payment['status']=='reported','Unverified transfer not found.',409)
            c.require(clean_amount(body.verified_amount)==Decimal(payment['amount']),
                      'Bank amount does not match.',422)
            reference=c.clean_text(body.bank_reference,64)
            c.require(not any(x.get('bank_reference')==reference
                              for x in state['upi_transfers'].values()),
                      'Bank reference was already reconciled.',409)
            payment.update(status='manually_reconciled',bank_reference=reference,
                           verified_by=user['email'],verified_at=c.now())
            # Never automatically mark an order invoice paid from customer-entered data.
            c.audit(state,user['email'],'UPI_MANUAL_RECONCILIATION',transfer_id)
            return {'transfer':payment,'message':'Manual bank receipt recorded; no order was auto-marked paid.'}

    @app.get('/customer/returns',tags=['Returns and refunds'])
    def customer_returns(request:Request):
        with c.read_transaction() as state:
            user,_=c.actor(state,request,'customer')
            return {'claims':c.mine(state,'rma',user)}

    @app.post('/customer/returns',tags=['Returns and refunds'])
    def create_return(request:Request,body:ReturnRequest):
        with c.transaction() as state:
            user,_=c.actor(state,request,'customer')
            order=c.owned(state,'orders',body.order_id,user)
            c.require(order['status']=='Delivered','Returns are available after delivery.',409)
            c.require(not any(x.get('order_id')==body.order_id and
                              x.get('status') not in {'rejected','cancelled'} for x in state['rma'].values()),
                      'An active return/refund request already exists for this order.',409)
            item={'id':c.uid('RMA'),'order_id':order['id'],'owner':user['email'],
                  'user_email':user['email'],'amount':order['total_amount'],
                  'reason':c.clean_text(body.reason,1500),'claim_type':body.request_type,
                  'status':'under_review','created_at':c.now()}
            state['rma'][item['id']]=item
            c.audit(state,user['email'],'CUSTOMER_RETURN_REQUESTED',item['id'])
            return {'claim':item}

    @app.get('/admin/returns',tags=['Return processing'])
    def admin_returns(request:Request):
        with c.read_transaction() as state:
            c.actor(state,request,'admin','finance')
            return {'claims':list(state['rma'].values())}

    @app.patch('/admin/returns/{return_id}',tags=['Return processing'])
    def review_return(return_id:str,request:Request,body:ReturnDecision):
        with c.transaction() as state:
            user,_=c.actor(state,request,'admin','finance')
            item=state['rma'].get(return_id)
            c.require(item,'Request not found.',404)
            transitions={'under_review':{'approved','rejected'},
                         'approved':{'received'}}
            c.require(body.status in transitions.get(item['status'],set()),
                      'Invalid return transition.',409)
            item.update(status=body.status,reviewed_at=c.now())
            c.audit(state,user['email'],'RETURN_'+body.status.upper(),return_id)
            c.notify(state,item['owner'],'Return update',
                     'Your return request '+return_id+' is '+body.status.replace('_',' ')+'.')
            return {'claim':item}

    @app.post('/admin/returns/{return_id}/refund-record',tags=['Return processing'])
    def record_refund(return_id:str,request:Request,body:RefundConfirmation):
        with c.transaction() as state:
            user,_=c.actor(state,request,'admin','finance')
            item=state['rma'].get(return_id)
            c.require(item and item['status'] in {'approved','received'},
                      'Approve the claim before recording a refund.',409)
            amount=clean_amount(body.amount)
            c.require(amount<=Decimal(str(item['amount'])),'Refund exceeds this order.',422)
            order=state['orders'].get(item['order_id'])
            c.require(order and order['payment_status'] in {'paid','cod_collected'},
                      'No confirmed payment is recorded for this order.',409)
            reference=c.clean_text(body.refund_reference,120)
            if body.method=='razorpay':
                c.require(order.get('provider_payment_id'),'No Razorpay payment for this order.',409)
                refund=c.razorpay_call('GET','refunds/'+reference)
                c.require(refund.get('status')=='processed' and
                          refund.get('payment_id')==order['provider_payment_id']
                          and refund.get('amount')==int(amount*100),
                          'Provider refund is not confirmed for this payment and amount.',409)
                outcome='refunded'
            else:
                # Manual bank/COD refunds require external bank statement verification;
                # retain them as a staff record rather than a verified provider payout.
                outcome='manual_refund_reported'
            item.update(status=outcome,refund_amount=str(amount),refund_reference=reference,
                        refund_method=body.method,refund_recorded_at=c.now())
            c.audit(state,user['email'],'REFUND_RECORDED_'+body.method.upper(),return_id)
            c.notify(state,item['owner'],'Refund update',
                     'Your refund record '+return_id+' was updated: '+outcome.replace('_',' ')+'.')
            return {'claim':item,'message':'Refund record updated. Manual transfers require external verification.'}
