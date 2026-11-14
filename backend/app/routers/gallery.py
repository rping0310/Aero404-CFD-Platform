"""Shared, authenticated wallpapers stored beneath the platform data directory.

Like the existing JSON job queue this store is intended for one app process.
Images are decoded/re-encoded, not served under the uploaded filename.
"""
import io
import json
import os
import re
import threading
import uuid
import warnings

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel

from ..config import settings
from ..dependencies import current_user, admin_user

router = APIRouter(prefix='/api/gallery', tags=['gallery'])
_lock = threading.RLock()
MAX_BYTES = 25 * 1024 * 1024
MAX_PIXELS = 24_000_000


def _directory():
    return settings.DATA_DIR / 'wallpapers'


def _read():
    path = _directory() / 'gallery.json'
    if not path.exists():
        return {'selected': 'alpine', 'photos': []}
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(data, dict) or not isinstance(data.get('photos'), list):
            raise ValueError('Invalid gallery index')
        return data
    except (ValueError, OSError) as exc:
        raise HTTPException(500, 'Cannot read the shared gallery index; check server storage') from exc


def _write(data):
    folder = _directory()
    folder.mkdir(parents=True, exist_ok=True)
    temporary = folder / f'.{uuid.uuid4().hex}.tmp'
    try:
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(temporary, folder / 'gallery.json')
    finally:
        temporary.unlink(missing_ok=True)


def _response(data):
    return {'selected': data['selected'], 'photos': [
        {'id': 'alpine', 'name': 'Alpine lake', 'url': '/static/img/alpine-lake.jpg'},
        *[{'id': p['id'], 'name': p['name'], 'url': f"/api/gallery/photos/{p['id']}"} for p in data['photos']]
    ]}


@router.get('')
def get_gallery(user=Depends(current_user)):
    with _lock:
        return _response(_read())


class Selection(BaseModel):
    id: str


@router.put('/selection')
def select_photo(body: Selection, user=Depends(admin_user)):
    with _lock:
        data = _read()
        if body.id not in {'alpine', *[p['id'] for p in data['photos']]}:
            raise HTTPException(404, 'Wallpaper not found')
        data['selected'] = body.id
        _write(data)
        return _response(data)


@router.post('/photos', status_code=201)
def upload_photo(file: UploadFile = File(...), user=Depends(admin_user)):
    if file.content_type not in {'image/jpeg', 'image/png', 'image/webp'}:
        raise HTTPException(415, 'Choose a JPG, PNG or WebP photo')
    payload = file.file.read(MAX_BYTES + 1)
    if len(payload) > MAX_BYTES:
        raise HTTPException(413, 'Photo must be smaller than 25 MB')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(payload)) as source:
                if source.format not in {'JPEG', 'PNG', 'WEBP'}:
                    raise ValueError('Unsupported image format')
                if source.width * source.height > MAX_PIXELS:
                    raise ValueError('Photo exceeds 24 megapixels')
                image = ImageOps.exif_transpose(source)
                image.thumbnail((2200, 2200))
                image = image.convert('RGB')
                output = io.BytesIO()
                image.save(output, format='JPEG', quality=88)
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise HTTPException(422, 'Invalid photo or image exceeds the 24 megapixel limit') from exc
    identity = uuid.uuid4().hex
    # Filename is only a display label; all paths use the generated identity.
    name = re.split(r'[/\\]', file.filename or 'Photo')[-1].rsplit('.', 1)[0][:120] or 'Photo'
    with _lock:
        data = _read()
        folder = _directory()
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f'{identity}.jpg'
        try:
            path.write_bytes(output.getvalue())
            data['photos'].append({'id': identity, 'name': name})
            data['selected'] = identity
            _write(data)
        except OSError as exc:
            path.unlink(missing_ok=True)
            raise HTTPException(500, 'Cannot save photo; check server storage permissions and free space') from exc
        return _response(data)


@router.get('/photos/{identity}')
def get_photo(identity: str, user=Depends(current_user)):
    if not re.fullmatch(r'[0-9a-f]{32}', identity):
        raise HTTPException(404, 'Wallpaper not found')
    path = _directory() / f'{identity}.jpg'
    if not path.is_file():
        raise HTTPException(404, 'Wallpaper not found')
    return FileResponse(path, media_type='image/jpeg', headers={'Cache-Control': 'private, max-age=3600', 'X-Content-Type-Options': 'nosniff'})
