from hermes_cli.config import load_config
import httpx
import json

cfg = load_config()
providers = cfg.get("custom_providers", [])
kintio = next((p for p in providers if p.get("name") == "kintio"), None)

if not kintio:
    print("Kintio not found")
    exit(1)

url = kintio.get("base_url").rstrip("/") + "/messages"
headers = {
    "x-api-key": kintio.get("api_key"),
    "anthropic-version": "2023-06-01",
    "content-type": "application/json"
}
data = {
    "model": "claude-opus-4.8",
    "messages": [{"role": "user", "content": "مرحبا ؟"}],
    "max_tokens": 100
}

print(f"Testing Kintio: {url}")
r = httpx.post(url, headers=headers, json=data)
print(f"Status: {r.status_code}")
print(f"Response: {r.text}")
