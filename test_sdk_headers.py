import modal

app = modal.App("test-sdk-headers")
image = modal.Image.debian_slim().pip_install("anthropic")

@app.function(image=image)
def fetch_kintio():
    import asyncio
    import httpx
    from anthropic import AsyncAnthropic
    
    # We will override httpx client
    class LoggingClient(httpx.AsyncClient):
        async def send(self, request, *args, **kwargs):
            print("--- REQUEST URL ---")
            print(request.url)
            print("--- REQUEST HEADERS ---")
            for k, v in request.headers.items():
                print(f"{k}: {v}")
            print("--- REQUEST BODY ---")
            print(request.read().decode("utf-8")[:200])
            response = await super().send(request, *args, **kwargs)
            print("--- RESPONSE STATUS ---", response.status_code)
            return response
            
    client = AsyncAnthropic(
        base_url="https://api.kintio.com/v1",
        api_key="sf_2aa126f77e6b318d4f64ee34a26ca6d927d83e9b518af8ca",
        http_client=LoggingClient()
    )
    
    system_prompt = "Hello "*100
    
    async def run():
        try:
            response = await client.messages.create(
                model="claude-opus-4.8",
                max_tokens=100,
                system=system_prompt,
                messages=[{"role": "user", "content": "مرحبا"}]
            )
            return 200, str(response.content)
        except Exception as e:
            return getattr(e, "status_code", "unknown"), str(e)
            
    return asyncio.run(run())

@app.local_entrypoint()
def main():
    status, text = fetch_kintio.remote()
    print(f"Status: {status}")

