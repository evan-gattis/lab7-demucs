
import os
import redis
from minio import Minio
import json

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
    print("Worker listening for jobs on Redis...")

    while True:
        # Block until a new job arrives
        _, message = r.blpop("toWorker", timeout=0)

        try:
            # Decode the JSON message sent by REST
            job = json.loads(message)

            # Extract job information
            songhash = job.get("hash", job.get("songhash"))
            model = job.get("model", "mdx_extra_q")
            callback = job.get("callback")

            if not songhash:
                print("Invalid job: missing song hash")
                continue

            print(f"Received job: {songhash}")
            print(f"Model: {model}")
            print(f"Callback: {callback}")

            # Download the MP3 from MinIO
            local_path = download_song(songhash)

            print(f"Song ready for processing: {local_path}")

            # TODO: Run Demucs (Step 5)

        except (json.JSONDecodeError, TypeError, ValueError) as e:
            print(f"Invalid job message: {e}")
        except Exception as e:
            print(f"Error handling job: {e}")


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

if __name__ == "__main__":
    test_connections()
    listen_for_jobs()
