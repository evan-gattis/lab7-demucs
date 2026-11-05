"""Lab 7 music separation REST frontend.

Configure using REDIS_HOST, REDIS_PORT, MINIO_ENDPOINT, MINIO_ACCESS_KEY,
MINIO_SECRET_KEY, MINIO_SECURE, MINIO_QUEUE_BUCKET, MINIO_OUTPUT_BUCKET.
"""
import base64
import binascii
import hashlib
import io
import json
import logging
import os
import re
from urllib.parse import urlparse

from flask import Flask, jsonify, request, send_file
from minio import Minio
from minio.error import S3Error
import redis
from redis.exceptions import RedisError

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = int(os.getenv('MAX_CONTENT_LENGTH', str(16 * 1024 * 1024)))
logging.basicConfig(level=os.getenv('LOG_LEVEL', 'INFO'))
logger = logging.getLogger('lab7-rest')

REDIS_HOST = os.getenv('REDIS_HOST', 'localhost')
REDIS_PORT = int(os.getenv('REDIS_PORT', '6379'))
REDIS_QUEUE_KEY = os.getenv('REDIS_QUEUE_KEY', 'toWorker')
REDIS_LOG_KEY = os.getenv('REDIS_LOG_KEY', 'logging')
MINIO_ENDPOINT = os.getenv('MINIO_ENDPOINT', 'localhost:9000')
MINIO_ACCESS_KEY = os.getenv('MINIO_ACCESS_KEY', 'minioadmin')
MINIO_SECRET_KEY = os.getenv('MINIO_SECRET_KEY', 'minioadmin')
MINIO_SECURE = os.getenv('MINIO_SECURE', 'false').lower() in ('1', 'true', 'yes')
QUEUE_BUCKET = os.getenv('MINIO_QUEUE_BUCKET', 'queue')
OUTPUT_BUCKET = os.getenv('MINIO_OUTPUT_BUCKET', 'output')

redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=0, decode_responses=True)
minio_client = Minio(MINIO_ENDPOINT, access_key=MINIO_ACCESS_KEY,
                     secret_key=MINIO_SECRET_KEY, secure=MINIO_SECURE)
HASH_RE = re.compile(r'^[a-f0-9]{64}$')
# README inconsistently calls the bass stem 'base.mp3'; support both.
TRACK_NAMES = {'bass.mp3', 'base.mp3', 'drums.mp3', 'vocals.mp3', 'other.mp3'}


def log_info(message):
    logger.info(message)
    try:
        redis_client.lpush(REDIS_LOG_KEY, message)
    except RedisError:
        logger.warning('Unable to add message to Redis logging list')


def error(message, status):
    return jsonify({'error': message}), status


def object_location(songhash, track):
    if track == 'base.mp3':
        track = 'bass.mp3'
    return OUTPUT_BUCKET, f'{songhash}-{track}'


@app.get('/')
def home():
    # Required so GKE ingress health checks have a successful endpoint.
    return '<h1>Music Separation Server</h1><p>Use a valid /apiv1 endpoint.</p>', 200


@app.post('/apiv1/separate')
def separate():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return error('Expected a JSON object', 400)
    encoded = data.get('mp3')
    model = data.get('model', 'mdx_extra_q')
    callback = data.get('callback')
    if not isinstance(encoded, str) or not encoded:
        return error('mp3 must be a non-empty base64 string', 400)
    if not isinstance(model, str) or not model.strip():
        return error('model must be a non-empty string', 400)
    if callback is not None and not isinstance(callback, (dict, str)):
        return error('callback must be a JSON object or string if provided', 400)
    try:
        audio = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        return error('mp3 is not valid base64', 400)
    if not audio:
        return error('mp3 decoded to an empty file', 400)
    songhash = hashlib.sha256(audio).hexdigest()
    object_name = f'{songhash}.mp3'
    # Store file in MinIO rather than passing large audio through Redis.
    try:
        minio_client.put_object(QUEUE_BUCKET, object_name, io.BytesIO(audio),
                                len(audio), content_type='audio/mpeg')
        # Keep message format simple and interoperable with our future worker.
        job = {'hash': songhash, 'songhash': songhash, 'model': model,
               'bucket': QUEUE_BUCKET, 'object': object_name}
        if callback is not None:
            job['callback'] = callback
        redis_client.lpush(REDIS_QUEUE_KEY, json.dumps(job))
    except (S3Error, RedisError, OSError) as exc:
        logger.exception('Unable to queue song %s', songhash)
        return error(f'Unable to queue song: {type(exc).__name__}', 503)
    log_info(f'Queued song {songhash} for separation using {model}')
    return jsonify({'hash': songhash, 'reason': 'Song enqueued for separation'}), 200


@app.get('/apiv1/queue/')
@app.get('/apiv1/queue')
def queue():
    try:
        entries = redis_client.lrange(REDIS_QUEUE_KEY, 0, -1)
        hashes = []
        for entry in entries:
            try:
                message = json.loads(entry)
                hashes.append(message.get('hash') or message.get('songhash') or entry)
            except (ValueError, TypeError):
                hashes.append(entry)
        return jsonify({'queue': hashes})
    except RedisError:
        logger.exception('Unable to read Redis queue')
        return error('Unable to read queue', 503)


@app.get('/apiv1/track/<songhash>/<track>')
def get_track(songhash, track):
    if not HASH_RE.fullmatch(songhash):
        return error('Invalid song hash', 400)
    if track not in TRACK_NAMES:
        return error('Unsupported track name', 400)
    bucket, obj = object_location(songhash, track)
    try:
        response = minio_client.get_object(bucket, obj)
        try:
            data = response.read()
        finally:
            response.close()
            response.release_conn()
    except S3Error as exc:
        if exc.code in ('NoSuchKey', 'NoSuchBucket', 'NoSuchObject'):
            return error('Track not found (processing may not be finished)', 404)
        logger.exception('MinIO read failure')
        return error('Unable to retrieve track', 503)
    return send_file(io.BytesIO(data), mimetype='audio/mpeg',
                     as_attachment=True, download_name=track)


@app.route('/apiv1/remove/<songhash>/<track>', methods=['GET', 'DELETE'])
def remove_track(songhash, track):
    if not HASH_RE.fullmatch(songhash):
        return error('Invalid song hash', 400)
    if track not in TRACK_NAMES:
        return error('Unsupported track name', 400)
    bucket, obj = object_location(songhash, track)
    try:
        # MinIO's remove_object is idempotent; check existence first to
        # produce an accurate 404 response.
        minio_client.stat_object(bucket, obj)
        minio_client.remove_object(bucket, obj)
    except S3Error as exc:
        if exc.code in ('NoSuchKey', 'NoSuchBucket', 'NoSuchObject'):
            return error('Track not found', 404)
        logger.exception('MinIO delete failure')
        return error('Unable to remove track', 503)
    log_info(f'Removed {obj}')
    return jsonify({'hash': songhash, 'track': track, 'reason': 'Track removed'})


@app.errorhandler(413)
def too_large(_exc):
    return error('Upload exceeds maximum request size', 413)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', '5000')))
