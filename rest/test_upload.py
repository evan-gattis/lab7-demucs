
import base64
import requests

# Use any small MP3 file
filename = "C:\\Users\\EvanGattis\\Desktop\\Cl\\Datacenter\\lab7-demucs\\data\\short-hop.mp3"

with open(filename, "rb") as f:
    encoded_mp3 = base64.b64encode(f.read()).decode("utf-8")

payload = {
    "mp3": encoded_mp3,
    "model": "mdx_extra_q"
}

response = requests.post(
    "http://localhost:80/apiv1/separate",
    json=payload
)

print("Status:", response.status_code)
print("Response:", response.json())
