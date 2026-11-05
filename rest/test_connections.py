
import redis
from minio import Minio

# Test Redis
r = redis.Redis(
    host="localhost",
    port=6379,
    db=0,
    decode_responses=True
)

print("Redis connected:", r.ping())

# Test MinIO
minio_client = Minio(
    "localhost:9000",
    access_key="rootuser",
    secret_key="rootpass123",
    secure=False
)

print("MinIO buckets:")
for bucket in minio_client.list_buckets():
    print(" -", bucket.name)
