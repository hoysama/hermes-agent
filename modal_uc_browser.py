import os
import modal

app = modal.App("hermes-uc-browser")

# Image with SeleniumBase + Chrome dependencies for UC Mode
sb_image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install(
        "git",
        "wget",
        "curl",
        "xvfb",
        "libnss3",
        "libgconf-2-4",
        "libasound2",
        "libglib2.0-0",
        "libgtk-3-0",
        "libx11-xcb1",
        "libxcb-dri3-0",
        "libdrm2",
        "libgbm1",
    )
    .pip_install(
        "seleniumbase",
        "fastapi",
        "uvicorn",
    )
    .run_commands(
        "echo 'Cache bust 2 - Update SeleniumBase (2026-09-06)'",
        "wget -q -O - https://dl-ssl.google.com/linux/linux_signing_key.pub | gpg --dearmor -o /usr/share/keyrings/google-chrome.gpg",
        "echo 'deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome.gpg] http://dl.google.com/linux/chrome/deb/ stable main' > /etc/apt/sources.list.d/google-chrome.list",
        "apt-get update && apt-get install -y google-chrome-stable",
        "seleniumbase install chromedriver",
    )
)

@app.function(
    image=sb_image,
    timeout=120,
)
@modal.fastapi_endpoint(method="POST", requires_proxy_auth=True)
async def extract(data: dict):
    """
    Extract content from any URL using SeleniumBase UC Mode (Undetected Driver).
    Bypasses Cloudflare Turnstile, bot protection, and extracts readable text.
    
    Payload: {"url": "https://example.com"}
    """
    from seleniumbase import Driver
    
    target_url = data.get("url", "https://fikra-app.pages.dev")
    if not target_url.startswith("http"):
        target_url = "https://" + target_url

    driver = None
    try:
        # Launch SeleniumBase UC Mode (Undetected ChromeDriver)
        driver = Driver(uc=True, headless=True)
        # Use uc_open_with_reconnect for reliable Cloudflare / Turnstile handling
        driver.uc_open_with_reconnect(target_url, reconnect_time=4)
        
        # Attempt to handle any visible captcha checkboxes if present
        try:
            driver.uc_gui_click_captcha()
        except Exception:
            pass
        
        driver.sleep(2)
        
        title = driver.get_title()
        page_source = driver.get_page_source()
        
        # Extract clean text from the body
        try:
            body_text = driver.get_text("body")
        except Exception:
            body_text = page_source[:5000]
        
        # Provide both readable content and snippet for Hermes tools
        snippet = body_text[:15000].strip() if body_text else page_source[:1000]
        
        return {
            "status": "success",
            "url": target_url,
            "title": title,
            "length": len(body_text or page_source),
            "text": body_text,
            "snippet": snippet,
            "html_length": len(page_source),
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}, 500
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass
