import modal
import asyncio
from anthropic import AsyncAnthropic

app = modal.App("test-kintio")
image = modal.Image.debian_slim().pip_install("anthropic")

@app.function(image=image)
def fetch_kintio():
    client = AsyncAnthropic(
        base_url="https://api.kintio.com/v1",
        api_key="sf_2aa126f77e6b318d4f64ee34a26ca6d927d83e9b518af8ca",
        default_headers={"x-api-key": "sf_2aa126f77e6b318d4f64ee34a26ca6d927d83e9b518af8ca"}
    )
    
    async def run():
        try:
            response = await client.messages.create(
                model="claude-opus-4.8",
                max_tokens=100,
                messages=[{"role": "user", "content": "مرحبا"}],
                stream=True
            )
            chunks = []
            async for chunk in response:
                chunks.append(str(chunk.type))
            return 200, ", ".join(chunks)
        except Exception as e:
            return getattr(e, "status_code", "unknown"), str(e)
            
    return asyncio.run(run())

@app.local_entrypoint()
def main():
    status, text = fetch_kintio.remote()
    print(f"Status: {status}")
    print(f"Text: {text[:500]}")

