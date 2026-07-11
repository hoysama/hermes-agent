import modal
import httpx

app = modal.App("test-kintio-url")
image = modal.Image.debian_slim().pip_install("httpx")

@app.function(image=image)
def fetch_kintio():
    url = "https://api.kintio.com/v1/v1/messages"
    
    headers = {
        "x-api-key": "sf_2aa126f77e6b318d4f64ee34a26ca6d927d83e9b518af8ca",
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    
    data = {
        "model": "claude-opus-4.8",
        "messages": [{"role": "user", "content": "مرحبا"}],
        "max_tokens": 100
    }
    
    try:
        r = httpx.post(url, headers=headers, json=data)
        return r.status_code, r.text
    except Exception as e:
        return str(e), ""

@app.local_entrypoint()
def main():
    status, text = fetch_kintio.remote()
    print(f"Status: {status}")
    print(f"Text: {text[:500]}")

