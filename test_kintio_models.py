import httpx
import asyncio

async def main():
    headers = {"x-api-key": "sf_2aa126f77e6b318d4f64ee34a26ca6d927d83e9b518af8ca"}
    async with httpx.AsyncClient() as client:
        r = await client.get("https://api.kintio.com/v1/models", headers=headers)
        print("Status:", r.status_code)
        try:
            print("JSON:", r.json())
        except Exception as e:
            print("Text:", r.text)

asyncio.run(main())
