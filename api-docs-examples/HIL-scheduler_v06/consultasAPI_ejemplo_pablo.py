# Script de prueba: API Istentore integration

import requests
import json

BASE_URL = "https://3mku48kfxf.execute-api.eu-south-2.amazonaws.com/default"
EMAIL = "i-STENTORE"
PASSWORD = "bvS7bumKj86uKNt"

# --- 1. Login ---
print("1. Login...")
login_payload = {"email": EMAIL, "password": PASSWORD}
login_headers = {"Content-Type": "application/json"}
login_resp = requests.post(f"{BASE_URL}/login", headers=login_headers, json=login_payload)

if login_resp.status_code != 200:
    print(f"   Error login: {login_resp.status_code} - {login_resp.text}")
    exit(1)

login_data = login_resp.json()
token = login_data.get("token")
if not token:
    print("   Error: no se recibió token en la respuesta.")
    exit(1)
print("Bearer", token)


url = f"{BASE_URL}/market_products"

print("2. Market Products Summary...")
headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}
mp_resp = requests.get(url, headers=headers)

if mp_resp.status_code != 200:
    print(f"   Error market_products: {mp_resp.status_code} - {mp_resp.text}")
    exit(1)

mp_data = mp_resp.json()
if isinstance(mp_data, list):
    items = mp_data
else:
    items = mp_data.get("market_products", mp_data)

print(json.dumps(items, indent=2, ensure_ascii=False))