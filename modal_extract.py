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
        "echo 'Cache bust 2 - Upgrade Crawl4AI upstream (2026-09-06)'",
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
    
    Payload:
    {
        "url": "https://example.com",
        "magic": true,
        "css_selector": "article",     # optional
        "word_count_threshold": 10      # optional
    }
    """
    from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode
    
    url = data.get("url")
    if not url:
        return {"status": "error", "message": "Missing 'url' parameter"}, 400

    browser_config = BrowserConfig(
        headless=True,
        verbose=False,
    )
    
    magic_enabled = data.get("magic", True)
    css_selector = data.get("css_selector", None)
    word_count_threshold = data.get("word_count_threshold", 10)
    wait_for = data.get("wait_for", None)
    js_code = data.get("js_code", None)

    run_config = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        magic=magic_enabled,
        word_count_threshold=word_count_threshold,
        remove_overlay_elements=True,
        css_selector=css_selector,
        wait_for=wait_for,
        js_code=js_code,
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
            
            # Prefer fit_markdown (high density filtered for LLMs) if available
            markdown_content = getattr(result, "fit_markdown", None) or result.markdown or ""
            metadata = getattr(result, "metadata", {}) or {}
            title = metadata.get("title", "") if isinstance(metadata, dict) else ""
            
            return {
                "status": "success",
                "url": url,
                "title": title,
                "markdown": markdown_content,
                "raw_markdown": result.markdown or "",
                "html_length": len(getattr(result, "cleaned_html", "") or ""),
                "markdown_length": len(markdown_content),
            }
    except Exception as e:
        return {"status": "error", "message": str(e)}, 500
