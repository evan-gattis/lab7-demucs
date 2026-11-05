
import base64
import requests

BASE_URL = "http://localhost"  # Kubernetes Ingress

with open(r"C:\Users\EvanGattis\Desktop\Cl\Datacenter\lab7-demucs\data\short-hop.mp3", "rb") as f:
    mp3 = base64.b64encode(f.read()).decode("utf-8")

response = requests.post(
    f"{BASE_URL}/apiv1/separate",
    json={
        "mp3": mp3,
        "model": "mdx_extra_q"
    }
)

print("Upload:", response.status_code)
print(response.json())

response = requests.get(f"{BASE_URL}/apiv1/queue/")
print("Queue:", response.status_code)
print(response.json())
