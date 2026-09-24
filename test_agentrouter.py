import yaml
import httpx

with open("/tmp/config_verify3.yaml", "r") as f:
    cfg = yaml.safe_load(f)
    
providers = cfg.get("custom_providers", [])
router = next((p for p in providers if p.get("name") == "agentrouter"), None)

if not router:
    print("agentrouter not found")
    exit(1)

url = router.get("base_url").rstrip("/") + "/chat/completions"
headers = {
    "Authorization": f"Bearer {router.get('api_key')}",
    "content-type": "application/json"
}
data = {
    "model": "gpt-5.5",
    "messages": [{"role": "user", "content": "مرحبا"}],
    "max_tokens": 100
}

print(f"Testing agentrouter: {url}")
r = httpx.post(url, headers=headers, json=data)
print(f"Status: {r.status_code}")
print(f"Response: {r.text}")
