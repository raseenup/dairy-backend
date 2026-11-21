"""Large campaign uploads: authorization, partial uploads, validation and video streaming."""
import io
import struct

from PIL import Image
import secure_backend as c
from test_secure_flows import client, account

MAX=50_000_000
CHUNK=2_000_000


def authorized():
    return account('editor@example.com','admin','9876543000',role='content_manager')[0]


def begin(headers,filename,size):
    return client.post('/admin/content-media/uploads',headers=headers,
        json={'filename':filename,'size_bytes':size})


def chunk(headers,ident,raw,offset=0):
    return client.post('/admin/content-media/uploads/'+ident+'/chunk?offset='+str(offset),
        headers={**headers,'Content-Type':'application/octet-stream'},content=raw)


def tiny_image():
    buf=io.BytesIO()
    Image.new('RGB',(4,5),(28,121,87)).save(buf,format='PNG')
    return buf.getvalue()


def atom(kind,data=b''):
    return struct.pack('>I4s',len(data)+8,kind)+data


def sample_mp4(length=CHUNK+179):
    # Minimal structurally valid test container; browser codec decoding is
    # tested separately from server-side atom/MIME validation.
    ftyp=atom(b'ftyp',b'isom'+b'\0\0\0\0'+b'mp41')
    moov=atom(b'moov')
    return ftyp+moov+atom(b'mdat',b'\0'*(length-len(ftyp)-len(moov)-8))


def test_access_control_file_limit_and_authorized_upload():
    user,_=account()
    admin=authorized()
    assert begin(user,'banner.png',123).status_code==403
    assert begin({},'banner.png',123).status_code==401
    assert begin(admin,'large.mp4',MAX+1).status_code==422
    assert begin(admin,'script.svg',100).status_code==422
    image=tiny_image()
    started=begin(admin,'fresh-banner.png',len(image))
    assert started.status_code==200,started.text
    ident=started.json()['id']
    assert started.json()['chunk_size']==CHUNK
    assert client.post('/admin/content-media/uploads/'+ident+'/complete',
         headers=admin).status_code==409
    assert chunk(user,ident,image).status_code==403
    assert chunk(admin,ident,image).status_code==200
    assert chunk(admin,ident,image).json()['reused'] is True
    assert client.get('/admin/content-media/uploads/'+ident,
           headers=admin).json()['offset']==len(image)
    result=client.post('/admin/content-media/uploads/'+ident+'/complete',headers=admin)
    assert result.status_code==200,result.text
    aid=result.json()['id']
    assert result.json()['mime_type']=='image/png'
    assert result.json()['size']==len(image)
    public=client.get('/media/campaign/'+aid)
    assert public.status_code==200 and public.content==image
    assert public.headers['accept-ranges']=='bytes'
    assert public.headers['x-content-type-options']=='nosniff'
    head=client.head('/media/campaign/'+aid)
    assert head.status_code==200 and int(head.headers['content-length'])==len(image)
    part=client.get('/media/campaign/'+aid,headers={'Range':'bytes=0-9'})
    assert part.status_code==206 and part.content==image[:10]
    assert part.headers['content-range']=='bytes 0-9/'+str(len(image))
    assert client.get('/media/campaign/'+aid,headers={'If-None-Match':public.headers['etag']}).status_code==304
    assert client.get('/media/campaign/'+aid,headers={'Range':'bytes=9000-'}).status_code==416


def test_resumable_video_upload_rejected_truncation_and_partial_playback():
    admin=authorized()
    payload=sample_mp4()
    started=begin(admin,'promo.mp4',len(payload))
    assert started.status_code==200,started.text
    ident=started.json()['id']
    assert chunk(admin,ident,payload[CHUNK:],CHUNK).status_code==409
    first=chunk(admin,ident,payload[:CHUNK],0)
    assert first.status_code==200,first.text
    assert first.json()['offset']==CHUNK
    assert chunk(admin,ident,payload[:CHUNK],0).json()['reused'] is True
    second=chunk(admin,ident,payload[CHUNK:],CHUNK)
    assert second.status_code==200,second.text
    assert second.json()['offset']==len(payload)
    created=client.post('/admin/content-media/uploads/'+ident+'/complete',headers=admin)
    assert created.status_code==200,created.text
    result=client.get(created.json()['url'],headers={'Range':'bytes=100-299'})
    assert result.status_code==206
    assert result.content==payload[100:300]
    assert result.headers['content-range']=='bytes 100-299/'+str(len(payload))
    assert client.get(created.json()['url'],headers={'Range':'bytes=-20'}).content==payload[-20:]
    with c.read_transaction(maintain=False) as s:
        assert len(s.get('campaign_upload_sessions',{}))==0


def test_reject_spoofed_mp4_and_disallow_publish_without_completed_media():
    admin=authorized()
    raw=b'\0\0\0\x10ftypisom' + b'X'*55
    session=begin(admin,'fake.mp4',len(raw)).json()['id']
    assert chunk(admin,session,raw).status_code==200
    finish=client.post('/admin/content-media/uploads/'+session+'/complete',headers=admin)
    assert finish.status_code==422
    with c.read_transaction(maintain=False) as s:
        assert len(s.get('campaign_media',{}))==0
    assert client.post('/admin/content-media/uploads/'+session+'/complete',
                       headers=admin).status_code==422
    assert client.delete('/admin/content-media/uploads/'+session,headers=admin).status_code==200
    assert client.get('/admin/content-media/uploads/'+session,headers=admin).status_code==404


def test_marketing_media_library_prevents_removal_of_active_content():
    admin=authorized()
    customer,_=account()
    image=tiny_image()
    start=begin(admin,'summer.png',len(image)).json()
    assert chunk(admin,start['id'],image).status_code==200
    asset=client.post('/admin/content-media/uploads/'+start['id']+'/complete',
        headers=admin).json()
    aid=asset['id']
    assert client.get('/admin/content-media/library',headers=customer).status_code==403
    listing=client.get('/admin/content-media/library',headers=admin)
    assert listing.status_code==200,listing.text
    assert listing.json()['assets'][0]['id']==aid
    assert listing.json()['assets'][0]['in_use'] is False
    assert listing.json()['max_file_bytes']==MAX
    published=client.post('/admin/content',headers=admin,json={
        'title':'Fresh summer offer','placement':'home','media_id':aid})
    assert published.status_code==200,published.text
    assert client.get('/admin/content-media/library',headers=admin).json()['assets'][0]['in_use'] is True
    assert client.delete('/admin/content-media/library/'+aid,
        headers=admin).status_code==409
    assert client.delete('/admin/content/'+published.json()['campaign']['id'],
        headers=admin).status_code==200
    deleted=client.delete('/admin/content-media/library/'+aid,headers=admin)
    assert deleted.status_code==200,deleted.text
    assert client.get('/media/campaign/'+aid).status_code==404
    assert client.get('/admin/content-media/library',
        headers=admin).json()['used_bytes']==0


def test_email_placement_must_not_embed_large_videos():
    admin=authorized()
    mp4=sample_mp4(200)
    start=begin(admin,'newsletter.mp4',len(mp4)).json()
    assert chunk(admin,start['id'],mp4).status_code==200
    asset=client.post('/admin/content-media/uploads/'+start['id']+'/complete',
        headers=admin)
    assert asset.status_code==200,asset.text
    response=client.post('/admin/content',headers=admin,json={
        'title':'Newsletter','placement':'email','media_id':asset.json()['id']})
    assert response.status_code==422


def test_identical_uploads_reuse_stored_asset_and_do_not_consume_extra_quota():
    admin=authorized()
    image=tiny_image()
    first=begin(admin,'one.png',len(image))
    assert first.status_code==200,first.text
    assert chunk(admin,first.json()['id'],image).status_code==200
    first_done=client.post('/admin/content-media/uploads/'+first.json()['id']+'/complete',headers=admin)
    assert first_done.status_code==200,first_done.text
    before=client.get('/admin/content-media/library',headers=admin).json()
    second=begin(admin,'another-name.png',len(image))
    assert second.status_code==200,second.text
    assert chunk(admin,second.json()['id'],image).status_code==200
    second_done=client.post('/admin/content-media/uploads/'+second.json()['id']+'/complete',headers=admin)
    assert second_done.status_code==200,second_done.text
    assert second_done.json()['id']==first_done.json()['id']
    assert second_done.json()['reused'] is True
    after=client.get('/admin/content-media/library',headers=admin).json()
    assert before['used_bytes']==after['used_bytes']
    assert len(before['assets'])==len(after['assets'])==1
    with c.read_transaction(maintain=False) as state:
        assert not state.get('campaign_upload_sessions',{})
