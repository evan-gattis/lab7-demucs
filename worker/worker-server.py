
import os
import redis
from minio import Minio
import json
import subprocess
import sys
from pathlib import Path
import requests

# Connection configuration
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))

MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "localhost:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "rootuser")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "rootpass123")

QUEUE_BUCKET = "queue"
OUTPUT_BUCKET = "output"

# Redis connection
r = redis.Redis(
    host=REDIS_HOST,
    port=REDIS_PORT,
    db=0,
    decode_responses=True
)

# MinIO connection
minio_client = Minio(
    MINIO_ENDPOINT,
    access_key=MINIO_ACCESS_KEY,
    secret_key=MINIO_SECRET_KEY,
    secure=False
)


def test_connections():
    # Test Redis
    print("Redis connected:", r.ping())

    # Test MinIO
    buckets = minio_client.list_buckets()
    print("MinIO buckets:", [bucket.name for bucket in buckets])



def listen_for_jobs():
    log_info("Worker listening for jobs...")

    while True:
        # Wait until a job arrives in Redis
        _, message = r.blpop("toWorker", timeout=0)

        try:
            # Decode Redis message
            job = json.loads(message)

            songhash = job.get("hash", job.get("songhash"))
            model = job.get("model") or "mdx_extra_q"
            callback = job.get("callback")

            if not songhash:
                log_error("Invalid job: missing song hash")
                continue

            log_info(f"Received job: {songhash}")

            # Step 4: Download original MP3
            local_path = download_song(songhash)
            log_info(f"Downloaded song: {songhash}")

            # Step 5: Run Demucs separation
            tracks_dir = separate_song(
                local_path, songhash, model
            )
            log_info(f"Separation completed: {songhash}")

            # Step 6: Upload four separated tracks
            upload_tracks(tracks_dir, songhash)
            log_info(f"Tracks uploaded: {songhash}")

            # Optional webhook callback
            send_callback(callback)

            log_info(f"Job completed successfully: {songhash}")

        except (json.JSONDecodeError, TypeError) as e:
            log_error(f"Invalid Redis job message: {e}")

        except Exception as e:
            log_error(f"Job processing failed: {e}")



def download_song(songhash):
    # Create a temporary directory for input MP3s
    os.makedirs("temp/input", exist_ok=True)

    # Local destination for the downloaded MP3
    local_path = os.path.join("temp", "input", f"{songhash}.mp3")

    # MinIO object name (must match the REST server's upload name)
    object_name = f"{songhash}.mp3"

    print(f"Downloading {object_name} from MinIO...")

    minio_client.fget_object(
        QUEUE_BUCKET,
        object_name,
        local_path
    )

    print(f"Downloaded MP3 to: {local_path}")
    return local_path


def separate_song(local_path, songhash, model):
    output_dir = Path("temp/output").resolve()

    # Run Demucs as a separate Python process
    command = [
        sys.executable,
        "-m", "demucs.separate",
        "--mp3",
        "-n", model,
        "--out", str(output_dir),
        str(Path(local_path).resolve())
    ]

    print(f"Running Demucs on {songhash}...")
    print("Command:", " ".join(command), flush=True)

    # Wait for the separation to finish
    subprocess.run(command, check=True)

    # Demucs outputs files under:
    # temp/output/<model>/<songhash>/
    tracks_dir = output_dir / model / songhash

    # Verify all four output files exist
    tracks = ["bass", "drums", "vocals", "other"]

    for track in tracks:
        track_path = tracks_dir / f"{track}.mp3"

        if not track_path.is_file():
            raise FileNotFoundError(
                f"Missing Demucs output: {track_path}"
            )

    print(f"Demucs separation completed: {songhash}")
    return str(tracks_dir)


def upload_tracks(tracks_dir, songhash):
    tracks = ["bass", "drums", "vocals", "other"]

    for track in tracks:
        local_path = os.path.join(
            tracks_dir, f"{track}.mp3"
        )

        object_name = f"{songhash}-{track}.mp3"

        print(f"Uploading {object_name} to MinIO...")

        minio_client.fput_object(
            OUTPUT_BUCKET,
            object_name,
            local_path,
            content_type="audio/mpeg"
        )

    print(f"All four tracks uploaded for {songhash}")


def log_info(message):
    print(f"[INFO] {message}", flush=True)

    try:
        r.lpush("logging", f"[WORKER INFO] {message}")
    except redis.RedisError as e:
        print(f"Redis logging failed: {e}", flush=True)


def log_error(message):
    print(f"[ERROR] {message}", flush=True)

    try:
        r.lpush("logging", f"[WORKER ERROR] {message}")
    except redis.RedisError as e:
        print(f"Redis logging failed: {e}", flush=True)


def send_callback(callback):
    if not callback:
        return

    # Supports a callback URL string or a dictionary.
    if isinstance(callback, str):
        url = callback
        payload = {}
    elif isinstance(callback, dict):
        url = callback.get("url")
        payload = callback.get("payload", {})
    else:
        log_error("Unsupported callback format")
        return

    if not url:
        log_error("Callback is missing a URL")
        return

    try:
        response = requests.post(
            url,
            json=payload,
            timeout=10
        )
        response.raise_for_status()
        log_info(f"Callback delivered to {url}")

    except requests.RequestException as e:
        log_error(f"Callback failed: {e}")

if __name__ == "__main__":
    test_connections()
    listen_for_jobs()
