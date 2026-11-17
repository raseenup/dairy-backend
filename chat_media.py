"""Private, bounded image/voice attachments for an authorised order conversation.

Bytes never enter the frequently-read application-state JSON. A media URL alone
grants no access: every download rechecks the current assignment and ownership.
"""
import base64
import io
import struct
import wave
from fastapi import HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal
from PIL import Image, ImageOps

MAX_BYTES = 1500000


class AttachmentUpload(BaseModel):
    model_config = ConfigDict(extra='forbid')
    filename: str = Field(min_length=1, max_length=150)
    kind: Literal['image', 'voice']
    data_base64: str = Field(min_length=1, max_length=2000000)


def core():
    import secure_backend
    return secure_backend


def image_bytes(raw):
    try:
        with Image.open(io.BytesIO(raw)) as image:
            core().require(image.format in {'JPEG', 'PNG', 'WEBP', 'GIF'} and image.width * image.height <= 12000000,
                'Use JPEG, PNG, WebP or GIF up to 12 megapixels.', 422)
            image.verify()
        with Image.open(io.BytesIO(raw)) as image:
            # Flatten animation and remove metadata, including camera GPS.
            clean = ImageOps.exif_transpose(image).convert('RGB')
            clean.thumbnail((1920, 1920))
            out = io.BytesIO()
            clean.save(out, format='JPEG', quality=85, optimize=True)
            core().require(len(out.getvalue()) <= MAX_BYTES, 'Choose a smaller image.', 422)
            return out.getvalue(), 'image/jpeg', '.jpg'
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(422, 'Upload a valid JPEG, PNG, WebP or GIF image.')


def webm_audio(raw):
    # Inspect EBML track metadata, never search compressed packets for video IDs.
    def vint(offset, marker=False):
        if offset >= len(raw) or not raw[offset]:
            raise ValueError()
        length = 1
        while length <= 8 and not raw[offset] & (1 << (8 - length)):
            length += 1
        if length > (4 if marker else 8) or offset + length > len(raw):
            raise ValueError()
        value = int.from_bytes(raw[offset:offset + length], 'big')
        return (value if marker else value & ((1 << (7 * length)) - 1)), length
    def elements(start, end):
        count = 0
        while start < end:
            ident, a = vint(start, True)
            size, b = vint(start + a)
            payload = start + a + b
            finish = end if size == (1 << (7 * b)) - 1 else payload + size
            if finish > end or finish < payload or count > 256:
                raise ValueError()
            yield ident, payload, finish
            start = finish
            count += 1
    try:
        segment = next((item for item in elements(0, len(raw)) if item[0] == 0x18538067), None)
        if not segment:
            return False
        tracks = next((item for item in elements(segment[1], segment[2]) if item[0] == 0x1654AE6B), None)
        if not tracks:
            return False
        found = False
        for ident, start, end in elements(tracks[1], tracks[2]):
            if ident != 0xAE:
                continue
            fields = {key: raw[a:b] for key, a, b in elements(start, end)}
            if int.from_bytes(fields.get(0x83, b'\x00'), 'big') != 2 or fields.get(0x86) not in {b'A_OPUS', b'A_VORBIS'}:
                return False
            found = True
        return found
    except (ValueError, IndexError, OverflowError):
        return False


def audio_type(raw):
    """Allow browser voice-recording containers; reject active markup and video.

Container checks do not claim a codec scan. Files remain private, are served as
audio with nosniff, and never rendered as HTML or executed by the application.
"""
    if raw.startswith(b'RIFF') and raw[8:12] == b'WAVE':
        try:
            with wave.open(io.BytesIO(raw)) as audio:
                if not (0 < audio.getnframes() / audio.getframerate() <= 120 and
                        1 <= audio.getnchannels() <= 2 and audio.getframerate() <= 96000):
                    raise ValueError()
                if len(audio.readframes(audio.getnframes())) != audio.getnframes() * audio.getnchannels() * audio.getsampwidth():
                    raise ValueError()
            return 'audio/wav', '.wav'
        except Exception:
            raise HTTPException(422, 'Use a valid voice recording up to two minutes.')
    if raw.startswith(b'OggS'):
        offset = 0
        while offset < len(raw):
            if raw[offset:offset + 4] != b'OggS' or offset + 27 > len(raw) or raw[offset + 4] != 0:
                raise HTTPException(422, 'Invalid Ogg recording.')
            count = raw[offset + 26]
            end = offset + 27 + count
            if end > len(raw):
                raise HTTPException(422, 'Invalid Ogg recording.')
            end += sum(raw[offset + 27:end])
            if end > len(raw):
                raise HTTPException(422, 'Invalid Ogg recording.')
            offset = end
        if (b'OpusHead' in raw[:4096] or b'\x01vorbis' in raw[:4096]) and b'theora' not in raw[:4096]:
            return 'audio/ogg', '.ogg'
    if raw.startswith(b'\x1a\x45\xdf\xa3') and len(raw) > 64 and b'webm' in raw[:128] and webm_audio(raw):
        return 'audio/webm', '.webm'
    if len(raw) > 64 and raw[4:8] == b'ftyp':
        offset, boxes = 0, []
        while offset < len(raw):
            if offset + 8 > len(raw):
                break
            length = struct.unpack('>I', raw[offset:offset + 4])[0]
            header = 8
            if length == 1 and offset + 16 <= len(raw):
                length = struct.unpack('>Q', raw[offset + 8:offset + 16])[0]
                header = 16
            if length == 0:
                length = len(raw) - offset
            if length < header or offset + length > len(raw):
                break
            boxes.append(raw[offset + 4:offset + 8])
            offset += length
        if offset == len(raw) and b'mdat' in boxes and b'moov' in boxes and b'soun' in raw and b'vide' not in raw:
            return 'audio/mp4', '.m4a'
    # MP3: skip a bounded ID3 tag, then require an MPEG audio frame header.
    offset = 0
    if raw.startswith(b'ID3') and len(raw) >= 10 and all(b < 128 for b in raw[6:10]):
        offset = 10 + sum(raw[6 + i] << (7 * (3 - i)) for i in range(4))
    frame = raw[offset:offset + 4]
    if len(frame) == 4 and frame[0] == 255 and frame[1] & 224 == 224 and frame[1] & 6 and frame[2] & 240 not in {0, 240} and frame[2] & 12 != 12:
        return 'audio/mpeg', '.mp3'
    raise HTTPException(422, 'Use an audio-only WebM/Opus, Ogg, M4A, MP3 or WAV recording up to 1.5 MB.')


def metadata(item):
    return {k: item[k] for k in ('id', 'kind', 'filename', 'mime_type', 'size', 'url')}


def install_chat_media(app):
    c = core()
    from operations import media_connection
    from workforce import chat_access, idempotency
    table = ('fmv_secure.' if c.DATABASE_URL else '') + 'order_chat_files'
    with media_connection() as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS ' + table + ' (id TEXT PRIMARY KEY, content ' +
            ('BYTEA' if c.DATABASE_URL else 'BLOB') + ' NOT NULL, mime_type TEXT NOT NULL)')

    @app.post('/orders/{order_id}/chat/attachments', tags=['Order conversations'])
    def upload(order_id: str, request: Request, body: AttachmentUpload):
        with c.read_transaction(maintain=False) as state:
            user, _ = c.actor(state, request)
            chat_access(state, user, order_id, True)
        c.rate_limit('chat-media:' + user['id'], 10, 60)
        c.require('/' not in body.filename and '\\' not in body.filename and
                  not any(ord(ch) < 32 for ch in body.filename), 'Choose a valid filename.', 422)
        try:
            raw = base64.b64decode(body.data_base64, validate=True)
        except Exception:
            raise HTTPException(422, 'Invalid attachment encoding.')
        c.require(0 < len(raw) <= MAX_BYTES, 'Choose an attachment up to 1.5 MB.', 422)
        if body.kind == 'image':
            raw, mime, suffix = image_bytes(raw)
        else:
            mime, suffix = audio_type(raw)
        ident = c.uid('CHATFILE')
        with media_connection() as conn:
            conn.execute(('INSERT INTO ' + table + ' VALUES (%s,%s,%s)' if c.DATABASE_URL else
                'INSERT INTO ' + table + ' VALUES (?,?,?)'), (ident, raw, mime))
        keep = False
        try:
            with c.transaction() as state:
                user, _ = c.actor(state, request)
                chat_access(state, user, order_id, True)
                existing, key, fingerprint = idempotency(state, 'chat_media_requests', request, user, body)
                if existing:
                    c.require(existing['order_id'] == order_id, 'Attachment key belongs to another order.', 409)
                    return metadata(state['chat_attachments'][existing['attachment_id']])
                records = state.setdefault('chat_attachments', {})
                c.require(sum(a['order_id'] == order_id for a in records.values()) < 100,
                    'This conversation reached its attachment limit. Contact support.', 409)
                item = dict(id=ident, order_id=order_id, uploader_id=user['id'], message_id='', kind=body.kind,
                    filename=('Photo' if body.kind == 'image' else 'Voice message') + suffix,
                    mime_type=mime, size=len(raw), url='/order-chat-media/' + ident, created_at=c.now())
                records[ident] = item
                state['chat_media_requests'][key] = dict(request_key=key, fingerprint=fingerprint, order_id=order_id, attachment_id=ident)
                c.audit(state, user['email'], 'ORDER_ATTACHMENT_UPLOADED', order_id)
            keep = True
            return metadata(item)
        finally:
            if not keep:
                with media_connection() as conn:
                    conn.execute('DELETE FROM ' + table + ' WHERE id=' + ('%s' if c.DATABASE_URL else '?'), (ident,))

    @app.get('/order-chat-media/{attachment_id}', tags=['Order conversations'])
    def download(attachment_id: str, request: Request):
        with c.read_transaction(maintain=False) as state:
            user, _ = c.actor(state, request)
            item = state.get('chat_attachments', {}).get(attachment_id)
            c.require(item, 'Attachment not found.', 404)
            chat_access(state, user, item['order_id'])
            c.require(item['message_id'] or item['uploader_id'] == user['id'], 'Attachment not found.', 404)
        with media_connection() as conn:
            row = conn.execute('SELECT content, mime_type FROM ' + table + ' WHERE id=' + ('%s' if c.DATABASE_URL else '?'), (attachment_id,)).fetchone()
        c.require(row, 'Attachment not found.', 404)
        return Response(bytes(row[0]), media_type=row[1], headers={'Cache-Control': 'no-store',
            'Content-Disposition': 'inline; filename="' + item['filename'] + '"', 'X-Content-Type-Options': 'nosniff',
            'Content-Security-Policy': "default-src 'none'; sandbox"})
