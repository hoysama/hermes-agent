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
@modal.fastapi_endpoint(method="POST")
async def extract(data: dict):
    from seleniumbase import Driver
    
    target_url = data.get("url", "https://fikra-app.pages.dev")
    if not target_url.startswith("http"):
        target_url = "https://" + target_url

    driver = None
    try:
        # Launch SeleniumBase UC Mode (Undetected ChromeDriver)
        driver = Driver(uc=True, headless=True)
        driver.get(target_url)
        driver.sleep(3)  # Wait for Cloudflare/JS checks to finish
        
        title = driver.get_title()
        page_source = driver.get_page_source()
        
        # Take screenshot or check elements
        return {
            "status": "success",
            "url": target_url,
            "title": title,
            "length": len(page_source),
            "snippet": page_source[:1000]
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}, 500
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass
