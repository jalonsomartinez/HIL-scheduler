# Script de prueba: API Istentore – SOLO CONSULTAS

import requests
import json

BASE_URL = "https://3mku48kfxf.execute-api.eu-south-2.amazonaws.com/default"
EMAIL = "i-STENTORE"
PASSWORD = "bvS7bumKj86uKNt"

# --------------------------------------------------
# 1. LOGIN
# --------------------------------------------------
print("1. Login...")
login_payload = {
    "email": EMAIL,
    "password": PASSWORD
}
login_headers = {
    "Content-Type": "application/json"
}

login_resp = requests.post(
    f"{BASE_URL}/login",
    headers=login_headers,
    json=login_payload
)

if login_resp.status_code != 200:
    print(f"   Error login: {login_resp.status_code} - {login_resp.text}")
    exit(1)

login_data = login_resp.json()
token = login_data.get("token")

if not token:
    print("   Error: no se recibió token en la respuesta.")
    exit(1)

print("   ✅ Token obtenido correctamente")
print("   Bearer", token)


# --------------------------------------------------
# 2. MARKET PRODUCTS (solo para inspección)
# --------------------------------------------------
print("\n2. Market Products...")
headers = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {token}"
}

mp_resp = requests.get(f"{BASE_URL}/market_products", headers=headers)

if mp_resp.status_code != 200:
    print(f"   Error market_products: {mp_resp.status_code} - {mp_resp.text}")
    exit(1)

mp_data = mp_resp.json()

print("   Market products response:")
print(json.dumps(mp_data, indent=2, ensure_ascii=False))


# --------------------------------------------------
# 3. MEASUREMENT SERIES (CLAVE PARA POSTEAR DESPUÉS)
# --------------------------------------------------
print("\n3. Measurement series...")

ms_resp = requests.get(f"{BASE_URL}/measurement_series", headers=headers)

if ms_resp.status_code != 200:
    print(f"   Error measurement_series: {ms_resp.status_code} - {ms_resp.text}")
    exit(1)

ms_data = ms_resp.json()

print("   Measurement series response:")
print(json.dumps(ms_data, indent=2, ensure_ascii=False))


print("\n✅ Consulta finalizada correctamente")
