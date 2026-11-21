"""Resumable public marketing asset uploads without base64 or transient disk storage.

Authenticated content editors upload one <=2MB binary part at a time.
The existing Postgres/SQLite media table holds validated immutable assets.
Large public videos support HTTP byte ranges, never buffering 50MB in RAM.
"""
from __future__ import annotations

import hashlib
import io
import os
import re
import time
from typing import Optional

from fastapi import Request, HTTPException
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from PIL import Image, UnidentifiedImageError

import secure_backend as c

MAX_FILE_BYTES = 50_000_000
PART_BYTES = 2_000_000
MAX_MEDIA_TOTAL = 120_000_000  # hard cap protects the existing free database
MAX_ACTIVE_PER_EDITOR = 2
SESSION_SECONDS = 3 * 3600
READ_BYTES = 1_000_000
ALLOWED_EXTENSIONS = {
    'image/jpeg': {'.jpg', '.jpeg'},
    'image/png': {'.png'},
    'image/gif': {'.gif'},
    'image/webp': {'.webp'},
    'video/mp4': {'.mp4'},
}
UPLOAD_ID = re.compile(r'^UPLOAD-[A-Za-z0-9_-]{8,100}$')
MEDIA_ID = re.compile(r'^MEDIA-[A-Za-z0-9_-]{8,100}$')

class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')

class UploadStart(Strict):
    filename: str = Field(min_length=3, max_length=160)
    size_bytes: int = Field(strict=True, ge=1, le=MAX_FILE_BYTES)

def conn():
    from operations import media_connection
    return media_connection()

def media_table():
    return ('fmv_secure.' if c.DATABASE_URL else '')+'campaign_files'

def chunk_table():
    return ('fmv_secure.' if c.DATABASE_URL else '')+'campaign_upload_parts'

def bind():
    return '%s' if c.DATABASE_URL else '?'

def allowed_upload(state, request):
    from experience import content_actor
    return content_actor(state,request)

def check_session(state, request, ident):
    c.require(bool(UPLOAD_ID.fullmatch(ident)),'Upload session not found.',404)
    user=allowed_upload(state,request)
    session=state.setdefault('campaign_upload_sessions',{}).get(ident)
    c.require(session and session['owner_id']==user['id'] and
              session['expires_at']>time.time(),'Upload session not found or expired.',404)
    return user,session

def used_bytes(state):
    return sum(max(0,int(a.get('size',0))) for a in state.get('campaign_media',{}).values())

def reserved_bytes(state):
    now=time.time()
    return sum(item['size_bytes'] for item in state.get('campaign_upload_sessions',{}).values()
               if item.get('expires_at',0)>now)

def size_of_chunks(conn_, ident):
    sql='SELECT COALESCE(SUM('+('OCTET_LENGTH' if c.DATABASE_URL else 'length')+
        '(content)),0) FROM '+chunk_table()+' WHERE upload_id='+bind()
    return int(conn_.execute(sql,(ident,)).fetchone()[0])

def collect_mime(raw:bytes, filename:str):
    suffix=os.path.splitext(filename.lower())[1]
    c.require(suffix in {e for exts in ALLOWED_EXTENSIONS.values() for e in exts},
              'Only JPEG, PNG, GIF, WebP and MP4 are supported.',422)
    if len(raw)>=16 and raw[4:8]==b'ftyp' and raw[8:12] in {
        b'isom',b'iso2',b'iso4',b'iso5',b'iso6',b'iso8',b'mp41',
        b'mp42',b'avc1',b'M4V ',b'dash',b'3gp4',b'3gp5'}:
        # Validate the outer MP4 boxes (the browser remains responsible for
        # codec support). Reject truncated, malformed and spoofed containers.
        at=0
        seen=set()
        while at<len(raw):
            c.require(at+8<=len(raw),'Truncated MP4 atom.',422)
            box_size=int.from_bytes(raw[at:at+4],'big')
            kind=bytes(raw[at+4:at+8])
            header=8
            if box_size==1:
                c.require(at+16<=len(raw),'Truncated extended MP4 atom.',422)
                box_size=int.from_bytes(raw[at+8:at+16],'big')
                header=16
            elif box_size==0:
                box_size=len(raw)-at
            c.require(box_size>=header and at+box_size<=len(raw),
                      'Malformed MP4 atom length.',422)
            seen.add(kind)
            at+=box_size
        c.require({b'ftyp',b'moov',b'mdat'}<=seen,
                  'MP4 must contain valid file, movie and media atoms.',422)
        mime='video/mp4'
    else:
        try:
            Image.MAX_IMAGE_PIXELS=45000000
            im=Image.open(io.BytesIO(raw))
            c.require(im.format in {'JPEG','PNG','GIF','WEBP'}
                and max(im.size)<=8192 and im.width*im.height<=40000000
                and getattr(im,'n_frames',1)<=300,
                'Use JPEG/PNG/GIF/WebP up to 8192 pixels, 40MP and 300 frames.',422)
            mime=Image.MIME[im.format]
            im.verify()
        except HTTPException:raise
        except Exception as error:
            raise HTTPException(422,'Invalid media file; use a genuine JPEG, PNG, GIF, WebP or MP4.') from error
    c.require(suffix in ALLOWED_EXTENSIONS.get(mime,set()),
              'The file extension does not match the verified media type.',422)
    return mime

def install_large_promotion_media(app):
    with conn() as con:
        con.execute('CREATE TABLE IF NOT EXISTS '+chunk_table()+
            ' (upload_id TEXT NOT NULL, sequence INTEGER NOT NULL, content '+
            ('BYTEA' if c.DATABASE_URL else 'BLOB')+
            ' NOT NULL, digest TEXT NOT NULL, created_epoch DOUBLE PRECISION NOT NULL, '+
            'PRIMARY KEY (upload_id,sequence))')
    with c.transaction() as state:
        state.setdefault('campaign_upload_sessions',{})

    @app.post('/admin/content-media/uploads',tags=['Resumable advertising uploads'])
    def begin_upload(request:Request,body:UploadStart):
        suffix=os.path.splitext(body.filename.lower())[1]
        c.require(suffix in {v for s in ALLOWED_EXTENSIONS.values() for v in s},
                  'Use JPEG, PNG, GIF, WebP or MP4.',422)
        with c.read_transaction(maintain=False) as state:
            user=allowed_upload(state,request)
        c.rate_limit('media-upload-create:'+user['id'],12,3600)
        with c.transaction() as state:
            user=allowed_upload(state,request)
            sessions=state.setdefault('campaign_upload_sessions',{})
            now=time.time()
            expired=[ident for ident,item in sessions.items() if item['expires_at']<=now]
            for ident in expired:sessions.pop(ident,None)
            active=sum(item['owner_id']==user['id'] for item in sessions.values())
            c.require(active<MAX_ACTIVE_PER_EDITOR,'Finish or cancel an unfinished media upload first.',409)
            c.require(used_bytes(state)+reserved_bytes(state)+body.size_bytes<=MAX_MEDIA_TOTAL,
                'Media library storage limit reached. Contact the administrator to free storage.',409)
            ident=c.uid('UPLOAD')
            sessions[ident]=dict(id=ident,owner_id=user['id'],owner_email=user['email'],
                filename=body.filename,size_bytes=body.size_bytes,created_at=c.now(),
                expires_at=now+SESSION_SECONDS)
            c.audit(state,user['email'],'PROMOTION_UPLOAD_STARTED',ident)
        # Removing previously expired parts is safe and never touches published media.
        with conn() as con:
            con.execute('DELETE FROM '+chunk_table()+' WHERE created_epoch < '+bind(),
                        (time.time()-SESSION_SECONDS,))
        return {'id':ident,'chunk_size':PART_BYTES,'offset':0,'total_size':body.size_bytes,
                'expires_at':now+SESSION_SECONDS}

    @app.get('/admin/content-media/uploads/{upload_id}',tags=['Resumable advertising uploads'])
    def upload_progress(upload_id:str,request:Request):
        with c.read_transaction(maintain=False) as state:
            _,session=check_session(state,request,upload_id)
            size=session['size_bytes']
        with conn() as con:offset=size_of_chunks(con,upload_id)
        c.require(offset<=size,'Upload state inconsistent; cancel and retry.',409)
        return {'id':upload_id,'offset':offset,'total_size':size,'chunk_size':PART_BYTES}

    @app.post('/admin/content-media/uploads/{upload_id}/chunk',tags=['Resumable advertising uploads'])
    async def receive_chunk(upload_id:str,request:Request,offset:int):
        content_type=request.headers.get('content-type','').lower().split(';',1)[0]
        c.require(content_type=='application/octet-stream','Send binary chunk bytes only.',415)
        size_header=request.headers.get('content-length','')
        c.require(size_header.isdigit() and 0<int(size_header)<=PART_BYTES,
                  'Chunk size must be 1 to 2 MB.',413)
        with c.read_transaction(maintain=False) as state:
            user,session=check_session(state,request,upload_id)
            total=session['size_bytes']
        c.rate_limit('media-chunk:'+user['id'],100,3600)
        c.require(offset>=0 and offset%PART_BYTES==0 and offset<total,
                  'Invalid upload part offset.',422)
        length=int(size_header)
        c.require(length==min(PART_BYTES,total-offset),
                  'Use the exact chunk size returned by the server.',422)
        # Stream the request; never call request.body() for a 50MB upload.
        data=bytearray()
        async for part in request.stream():
            data.extend(part)
            c.require(len(data)<=PART_BYTES,'Chunk is too large.',413)
        c.require(len(data)==length,'Incomplete upload chunk; retry this part.',422)
        digest=hashlib.sha256(data).hexdigest()
        with conn() as con:
            current=size_of_chunks(con,upload_id)
            if current>offset:
                existing=con.execute('SELECT digest FROM '+chunk_table()+
                    ' WHERE upload_id='+bind()+' AND sequence='+bind(),
                    (upload_id,offset//PART_BYTES)).fetchone()
                c.require(existing and existing[0]==digest,
                          'A different chunk is already uploaded at this position.',409)
                return {'offset':current,'total_size':total,'reused':True}
            c.require(current==offset,'Upload is out of sequence. Refresh its progress.',409)
            sql='INSERT INTO '+chunk_table()+
                ' (upload_id,sequence,content,digest,created_epoch) VALUES ('+
                ','.join([bind()]*5)+')'
            con.execute(sql,(upload_id,offset//PART_BYTES,bytes(data),digest,time.time()))
        return {'offset':offset+length,'total_size':total}

    @app.post('/admin/content-media/uploads/{upload_id}/complete',tags=['Resumable advertising uploads'])
    def finish_upload(upload_id:str,request:Request):
        with c.transaction() as state:
            user,session=check_session(state,request,upload_id)
            c.require(not session.get('finalizing'),'Upload is already being completed.',409)
            session['finalizing']=True
            filename=session['filename']
            expected=session['size_bytes']
        ident=None
        try:
            # Read one 2MB part at a time rather than fetching ~50MB twice.
            raw=bytearray()
            digest_stream=hashlib.sha256()
            with conn() as con:
                c.require(size_of_chunks(con,upload_id)==expected,
                          'Upload incomplete. Resume missing chunks.',409)
                cursor=con.execute('SELECT content FROM '+chunk_table()+
                    ' WHERE upload_id='+bind()+' ORDER BY sequence',(upload_id,))
                while True:
                    part=cursor.fetchone()
                    if not part:break
                    segment=bytes(part[0])
                    raw.extend(segment)
                    digest_stream.update(segment)
                    c.require(len(raw)<=MAX_FILE_BYTES,'Media exceeds 50 MB.',413)
            c.require(len(raw)==expected,'Uploaded media size mismatch.',422)
            mime=collect_mime(raw,filename)
            digest=digest_stream.hexdigest()
            ident=c.uid('MEDIA')
            with conn() as con:
                con.execute('INSERT INTO '+media_table()+' (id,content,mime_type) VALUES ('+
                    ','.join([bind()]*3)+')',(ident,bytes(raw),mime))
            del raw
            with c.transaction() as state:
                user,session=check_session(state,request,upload_id)
                c.require(used_bytes(state)+expected<=MAX_MEDIA_TOTAL,
                          'Storage capacity reached. Cancel another draft upload.',409)
                state.setdefault('campaign_media',{})[ident]={
                    'id':ident,'mime_type':mime,'size':expected,'sha256':digest,
                    'filename':filename,'created_at':c.now(),'created_by':user['email']}
                state['campaign_upload_sessions'].pop(upload_id,None)
                c.audit(state,user['email'],'CAMPAIGN_MEDIA_UPLOADED',ident)
            with conn() as con:
                con.execute('DELETE FROM '+chunk_table()+' WHERE upload_id='+bind(),(upload_id,))
            return {'id':ident,'url':'/media/campaign/'+ident,'mime_type':mime,
                    'size':expected,'sha256':digest}
        except BaseException:
            if ident:
                with conn() as con:
                    con.execute('DELETE FROM '+media_table()+' WHERE id='+bind(),(ident,))
            with c.transaction() as state:
                session=state.setdefault('campaign_upload_sessions',{}).get(upload_id)
                if session:session.pop('finalizing',None)
            raise

    @app.delete('/admin/content-media/uploads/{upload_id}',tags=['Resumable advertising uploads'])
    def cancel_upload(upload_id:str,request:Request):
        with c.transaction() as state:
            user,_=check_session(state,request,upload_id)
            state['campaign_upload_sessions'].pop(upload_id,None)
            c.audit(state,user['email'],'PROMOTION_UPLOAD_CANCELLED',upload_id)
        with conn() as con:
            con.execute('DELETE FROM '+chunk_table()+' WHERE upload_id='+bind(),(upload_id,))
        return {'message':'Unpublished upload cancelled.'}

    # The old asset endpoint fetched the entire video into RAM, causing slow
    # startup/playback. Preserve its path and immutable URLs while streaming
    # directly from DB in bounded chunks, including partial-range playback.
    app.router.routes[:]=[route for route in app.routes if not (
        getattr(route,'path','')=='/media/campaign/{media_id}' and
        'GET' in getattr(route,'methods',set()))]

    @app.api_route('/media/campaign/{media_id}',methods=['GET','HEAD'],
                   tags=['Public advertising assets'])
    def campaign_asset(media_id:str,request:Request):
        c.require(bool(MEDIA_ID.fullmatch(media_id)),'Asset not found.',404)
        with c.read_transaction(maintain=False) as state:
            metadata=state.get('campaign_media',{}).get(media_id)
        c.require(metadata,'Asset not found.',404)
        size=metadata.get('size')
        with conn() as con:
            sql='SELECT mime_type'+((', OCTET_LENGTH(content)' if c.DATABASE_URL
                else ', length(content)'))+' FROM '+media_table()+' WHERE id='+bind()
            row=con.execute(sql,(media_id,)).fetchone()
        c.require(row,'Asset not found.',404)
        mime,total=row[0],int(row[1])
        c.require(total>0,'Empty media asset.',404)
        etag='"'+str(metadata.get('sha256') or media_id)+'"'
        common={'Cache-Control':'public, max-age=31536000, immutable',
            'X-Content-Type-Options':'nosniff','Accept-Ranges':'bytes','ETag':etag,
            'Content-Security-Policy':"default-src 'none'; sandbox"}
        if request.headers.get('if-none-match')==etag and not request.headers.get('range'):
            return Response(status_code=304,headers=common)
        begin,end,status=0,total-1,200
        range_header=request.headers.get('range')
        if range_header:
            match=re.fullmatch(r'bytes=(\d*)-(\d*)',range_header.strip())
            if not match or (not match.group(1) and not match.group(2)):
                raise HTTPException(416,'Invalid media byte range.')
            first,last=match.groups()
            if first:
                begin=int(first)
                if begin>=total:raise HTTPException(416,'Media byte range exceeds file size.')
                end=min(total-1,int(last)) if last else total-1
                if end<begin:raise HTTPException(416,'Invalid media byte range.')
            else:
                count=int(last)
                if count<1:raise HTTPException(416,'Invalid media byte range.')
                begin=max(0,total-count)
            status=206
            # Small 1MB responses support fast seek/stream, reduce DB egress and
            # avoid monopolizing the shared Postgres connection pool.
            end=min(end,begin+READ_BYTES-1)
            common['Content-Range']=f'bytes {begin}-{end}/{total}'
        common['Content-Length']=str(end-begin+1)
        if request.method=='HEAD':
            return Response(status_code=status,media_type=mime,headers=common)
        def chunks():
            position=begin
            while position<=end:
                count=min(READ_BYTES,end-position+1)
                with conn() as con:
                    sql='SELECT '+('substring(content from %s for %s)' if c.DATABASE_URL
                        else 'substr(content,?,?)')+' FROM '+media_table()+' WHERE id='+bind()
                    blob=con.execute(sql,(position+1,count,media_id)).fetchone()
                if not blob or not blob[0]:break
                data=bytes(blob[0]);position+=len(data);yield data
        return StreamingResponse(chunks(),status_code=status,media_type=mime,headers=common)
