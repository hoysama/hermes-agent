import os
import modal

APP_NAME = "hermes-web-extractor"

app = modal.App(APP_NAME)

# Image definition with Crawl4AI and Playwright dependencies
extract_image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install(
        "git",
        "curl",
        "wget",
        "ffmpeg",
        "libsm6",
        "libxext6",
        "libglib2.0-0",
        "libnss3",
        "libgconf-2-4",
        "libasound2",
    )
    .pip_install(
        "crawl4ai",
        "playwright",
        "fastapi",
        "uvicorn",
        "pydantic",
    )
    .run_commands(
        "python -m playwright install --with-deps chromium",
        "crawl4ai-setup",
    )
)

@app.function(
    image=extract_image,
    timeout=120,
    min_containers=0,
)
@modal.fastapi_endpoint(method="POST", requires_proxy_auth=True)
async def extract(data: dict):
    """
    Extract clean LLM-fit markdown from any web URL using Crawl4AI.
    
    Payload: {"url": "https://example.com"}
    """
    from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode
    
    url = data.get("url")
    if not url:
        return {"status": "error", "message": "Missing 'url' parameter"}, 400

    browser_config = BrowserConfig(
        headless=True,
        verbose=False,
    )
    
    run_config = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        word_count_threshold=10,
        remove_overlay_elements=True,
    )

    try:
        async with AsyncWebCrawler(config=browser_config) as crawler:
            result = await crawler.arun(url=url, config=run_config)
            
            if not result.success:
                return {
                    "status": "error",
                    "url": url,
                    "message": f"Extraction failed: {result.error_message}",
                }, 500
                
            return {
                "status": "success",
                "url": url,
                "title": result.metadata.get("title") if result.metadata else "",
                "markdown": result.markdown,
                "html_length": len(result.cleaned_html or "") if hasattr(result, "cleaned_html") and result.cleaned_html else 0,
                "markdown_length": len(result.markdown or ""),
            }
    except Exception as e:
        return {"status": "error", "message": str(e)}, 500
