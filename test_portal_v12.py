"""v12 regressions: private chat media, dated payroll, ad sizing and small reads."""
import base64
import io
import json
import uuid
import wave
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image
from main import app
import secure_backend as sec
from payroll import boundary
from email_templates import build_email
from test_secure_flows import account, checkout, photo
from test_portal_fixes import delivered_cash

client = TestClient(app)


def chat_order():
    customer, admin, driver, user, _ = delivered_cash()
    order = checkout(customer, key='private-chat-order-1234567').json()['order']
    client.patch('/admin/deliveries/' + order['id'] + '/assign', headers=admin, json={'driver_key': user['id']})
    return customer, admin, driver, user, order


def raw_photo():
    out = io.BytesIO()
    image = Image.new('RGB', (40, 40), 'green')
    exif = image.getexif()
    exif[270] = 'Private camera metadata'
    image.save(out, 'JPEG', exif=exif)
    return out.getvalue()


def raw_voice():
    out = io.BytesIO()
    with wave.open(out, 'wb') as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(b'\x00\x00' * 800)
    return out.getvalue()


def upload(headers, order, raw, kind='image', key=None):
    return client.post('/orders/' + order['id'] + '/chat/attachments',
        headers={**headers, 'Idempotency-Key': key or uuid.uuid4().hex},
        json={'filename': 'upload.jpg' if kind == 'image' else 'voice.wav', 'kind': kind,
              'data_base64': base64.b64encode(raw).decode()})


def send(headers, order, media_id, **extra):
    return client.post('/orders/' + order['id'] + '/chat', headers={**headers, 'Idempotency-Key': uuid.uuid4().hex},
        json=dict(message='', attachments=[media_id], **extra))


def test_chat_image_is_private_metadata_free_and_order_scoped():
    customer, admin, driver, user, order = chat_order()
    other, _ = account('outsider@example.com', phone='9876543213')
    media = upload(customer, order, raw_photo())
    assert media.status_code == 200, media.text
    item = media.json()
    assert item['mime_type'] == 'image/jpeg'
    assert client.get(item['url'], headers=driver).status_code == 404  # not sent yet
    assert send(customer, order, item['id']).status_code == 200
    assert client.get(item['url']).status_code == 401
    assert client.get(item['url'], headers=other).status_code == 404
    downloaded = client.get(item['url'], headers=driver)
    assert downloaded.status_code == 200
    assert downloaded.headers['cache-control'] == 'no-store'
    assert downloaded.headers['x-content-type-options'] == 'nosniff'
    assert 'content-encoding' not in downloaded.headers
    assert b'Private camera metadata' not in downloaded.content
    assert not Image.open(io.BytesIO(downloaded.content)).getexif()
    with sec.read_transaction() as state:
        assert 'data_base64' not in json.dumps(state['chat_attachments'])
    replacement, replacement_user = account('replacement@example.com', 'driver', '9876543214')
    client.patch('/admin/deliveries/' + order['id'] + '/assign', headers=admin, json={'driver_key': replacement_user['id']})
    assert client.get(item['url'], headers=driver).status_code == 404
    assert client.get(item['url'], headers=replacement).status_code == 200


def test_voice_can_be_sent_without_text_and_ownership_cannot_be_forged():
    customer, _, driver, _, order = chat_order()
    response = upload(driver, order, raw_voice(), 'voice')
    assert response.status_code == 200, response.text
    item = response.json()
    assert send(customer, order, item['id']).status_code == 404
    assert send(driver, order, item['id']).status_code == 200
    assert send(driver, order, item['id']).status_code == 409
    result = client.get(item['url'], headers=customer)
    assert result.content == raw_voice() and result.headers['content-type'].startswith('audio/wav')
    assert client.post('/orders/' + order['id'] + '/chat', headers=customer, json={'message': ''}).status_code == 422


def test_browser_voice_containers_accept_real_opus_and_aac_samples():
    customer, _, driver, _, order = chat_order()
    # Quarter-second synthetic tone encoded by FFmpeg; no microphone/user data.
    samples = json.loads(Path(__file__).with_name('test_media_samples.json').read_text())
    for suffix, mime in [('webm', 'audio/webm'), ('m4a', 'audio/mp4'), ('ogg', 'audio/ogg')]:
        response = upload(driver, order, base64.b64decode(samples[suffix]), 'voice')
        assert response.status_code == 200, (suffix, response.text)
        assert response.json()['mime_type'] == mime
        assert send(driver, order, response.json()['id']).status_code == 200
        assert client.get(response.json()['url'], headers=customer).content == base64.b64decode(samples[suffix])


def test_attachment_retry_is_idempotent_and_rejects_markup_bad_encoding_and_oversize():
    customer, _, driver, _, order = chat_order()
    first = upload(customer, order, raw_photo(), key='chat-upload-retry-123456789')
    second = upload(customer, order, raw_photo(), key='chat-upload-retry-123456789')
    assert first.json()['id'] == second.json()['id']
    for kind in ['image', 'voice']:
        assert upload(customer, order, b'<svg onload="alert(1)"/>', kind).status_code == 422
    assert upload(customer, order, b'A' * 1500001, 'voice').status_code == 422
    path = '/orders/' + order['id'] + '/chat/attachments'
    assert client.post(path, headers=customer, json={'filename': '../file', 'kind': 'voice', 'data_base64': '***'}).status_code == 422
    with sec.transaction() as state:
        state['orders'][order['id']]['status'] = 'Cancelled'
    assert upload(driver, order, raw_voice(), 'voice').status_code == 409


def test_chat_cursor_only_returns_new_messages_and_read_cursor_never_advances_unseen():
    customer, _, driver, _, order = chat_order()
    path = '/orders/' + order['id'] + '/chat'
    for message in ['First message', 'Second message']:
        assert client.post(path, headers={**customer, 'Idempotency-Key': uuid.uuid4().hex}, json={'message': message}).status_code == 200
    result = client.get(path + '?after=1', headers=driver).json()
    assert [m['message'] for m in result['messages']] == ['Second message']
    assert result['last_sequence'] == 2 and not result['has_more']
    assert client.get(path + '?after=2', headers=driver).json()['messages'] == []
    assert client.get(path + '?after=-1', headers=driver).status_code == 422
    assert client.post(path + '/read', headers=driver, json={'sequence': 3}).status_code == 422
    assert client.post(path + '/read', headers=driver, json={'sequence': 1}).status_code == 200
    assert client.get('/portal/snapshot', headers=driver).json()['chat_unread'][order['id']] == 1


def save_rule(admin, driver_id, **values):
    return client.patch('/admin/drivers/' + driver_id + '/pay-rule', headers=admin,
        json=dict(base_pay=200, peak_bonus=5, peak_hours=list(range(24)), **values))


def test_fixed_pay_only_credits_completed_period_once_after_accounts_confirmation(monkeypatch):
    _, admin, driver, user, _ = delivered_cash()
    start = date.fromisoformat(sec.today())
    assert save_rule(admin, user['id'], pay_basis='weekly').status_code == 200
    path = '/admin/drivers/' + user['id'] + '/pay-periods'
    body = {'period_start': start.isoformat(), 'confirmed': True}
    assert client.post(path, headers=admin, json=body).status_code == 409
    monkeypatch.setattr(sec, 'today', lambda: (start + timedelta(days=7)).isoformat())
    wallet = client.get('/portal/snapshot', headers=driver).json()['workforce']['wallet']
    assert wallet['due_pay_periods'][0]['amount'] == 200
    assert client.post(path, headers=driver, json=body).status_code == 403
    assert client.post(path, headers=admin, json=dict(body, confirmed=False)).status_code == 422
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: client.post(path, headers=admin, json=body), range(2)))
    assert all(r.status_code == 200 for r in results)
    assert results[0].json()['id'] == results[1].json()['id']
    wallet = client.get('/portal/snapshot', headers=driver).json()['workforce']['wallet']
    assert wallet['breakdown']['base_pay'] == 200 and not wallet['due_pay_periods']


def test_monthly_anniversary_handles_short_months_and_rules_cannot_overlap(monkeypatch):
    assert boundary({'effective_from': '2027-01-31', 'pay_basis': 'monthly'}, 1) == date(2027, 2, 28)
    assert boundary({'effective_from': '2027-01-31', 'pay_basis': 'monthly'}, 2) == date(2027, 3, 31)
    _, admin, _, user, _ = delivered_cash()
    start = date.fromisoformat(sec.today())
    assert save_rule(admin, user['id'], pay_basis='weekly').status_code == 200
    assert save_rule(admin, user['id'], pay_basis='daily', effective_from=(start + timedelta(days=1)).isoformat()).status_code == 409
    assert save_rule(admin, user['id'], pay_basis='daily', effective_from=(start + timedelta(days=7)).isoformat()).status_code == 200
    assert save_rule(admin, user['id'], pay_basis='daily', effective_from=(start - timedelta(days=1)).isoformat()).status_code == 422


def test_fixed_or_disabled_base_never_awards_delivery_base_and_peak_bonus_remains():
    customer, admin, driver, user, _ = delivered_cash()
    assert save_rule(admin, user['id'], pay_basis='monthly').status_code == 200
    order = checkout(customer, key='fixed-salary-delivery-1234567').json()['order']
    assert client.patch('/admin/deliveries/' + order['id'] + '/assign', headers=admin, json={'driver_key': user['id']}).status_code == 200
    delivered = client.patch('/driver/deliveries/' + order['id'] + '/epod', headers=driver, json={
        'status': 'Delivered', 'delivery_otp': order['delivery_otp'], 'epod_photo_url': photo(), 'cod_collected': order['cod_amount']})
    assert delivered.status_code == 200, delivered.text
    entries = client.get('/portal/snapshot', headers=driver).json()['workforce']['wallet']['entries']
    assert [e['category'] for e in entries] == ['peak_bonus']
    other, other_user = account('other-driver@example.com', 'driver', '9876543213')
    assert save_rule(admin, other_user['id'], pay_basis='none').status_code == 200
    wallet = client.get('/portal/snapshot', headers=other).json()['workforce']['wallet']
    assert wallet['pay_rule']['base_pay_paise'] == 0


def test_ad_strip_dimensions_are_validated_and_media_bytes_stay_out_of_snapshot():
    customer, _ = account()
    admin, _ = account('admin@example.com', 'admin', '9876543211')
    upload_result = client.post('/admin/content-media', headers=admin, json={
        'filename': 'advert.jpg', 'data_base64': base64.b64encode(raw_photo()).decode()})
    assert upload_result.status_code == 200, upload_result.text
    media = upload_result.json()
    body = dict(title='Fresh essentials', placement='home_location', media_id=media['id'],
        display_width=1000, display_height=200, media_fit='contain')
    assert client.post('/admin/content', headers=customer, json=body).status_code == 403
    assert client.post('/admin/content', headers=admin, json=dict(body, display_width='1000')).status_code == 422
    assert client.post('/admin/content', headers=admin, json=dict(body, display_height=2000)).status_code == 422
    created = client.post('/admin/content', headers=admin, json=body)
    assert created.status_code == 200, created.text
    campaign = client.get('/content', headers=customer).json()['campaigns'][0]
    assert campaign['placement'] == 'home_location' and campaign['display_width'] == 1000
    assert client.get(media['url']).content == raw_photo()
    with sec.read_transaction() as state:
        assert 'data_base64' not in state['campaign_media'][media['id']]
    snapshot = client.get('/portal/snapshot', headers=customer)
    assert 'data_base64' not in snapshot.text and snapshot.headers.get('content-encoding') == 'gzip'


def test_otp_email_has_contiguous_selectable_code_and_keyboard_fallback():
    for kind in ['verification', 'signup_verification', 'signin_verification', 'password_reset']:
        mail = build_email(kind, code='123456')
        assert mail['text'].startswith('123456 is your First Milk verification code.')
        assert 'dir="ltr" aria-label="Verification code">123456</p>' in mail['html']
        assert 'Paste code' in mail['html'] and 'device settings' in mail['text']
        assert '123456' not in mail['subject']
