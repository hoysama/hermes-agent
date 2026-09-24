"""Crawl4AI web content extraction provider using Modal Serverless endpoint."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Dict, List, Optional

import httpx

from agent.web_search_provider import WebSearchProvider
from tools.url_safety import is_safe_url

logger = logging.getLogger(__name__)

DEFAULT_CRAWL4AI_ENDPOINT = "https://hoysama--hermes-web-extractor-extract.modal.run"
DEFAULT_UC_BROWSER_ENDPOINT = "https://hoysama--hermes-uc-browser-extract.modal.run"


class Crawl4AIWebSearchProvider(WebSearchProvider):
    """Web extraction provider using Crawl4AI Modal endpoint with SeleniumBase UC Browser fallback."""

    @property
    def name(self) -> str:
        return "crawl4ai"

    @property
    def display_name(self) -> str:
        return "Crawl4AI + UC Browser (Modal Cloud)"

    def supports_search(self) -> bool:
        return False

    def supports_extract(self) -> bool:
        return True

    def is_available(self) -> bool:
        return True

    async def extract(
        self,
        urls: List[str],
        format: str = "markdown",
        **kwargs: Any,
    ) -> List[Dict[str, Any]]:
        """Extract content from multiple URLs concurrently via Crawl4AI Modal Endpoint with UC Browser fallback."""
        endpoint = os.getenv("CRAWL4AI_MODAL_URL", DEFAULT_CRAWL4AI_ENDPOINT)
        uc_endpoint = os.getenv("UC_BROWSER_MODAL_URL", DEFAULT_UC_BROWSER_ENDPOINT)
        
        async with httpx.AsyncClient(timeout=60.0) as client:
            tasks = [self._extract_single(client, url, endpoint, uc_endpoint) for url in urls]
            results = await asyncio.gather(*tasks)
            
        return list(results)

    async def _extract_single(
        self, client: httpx.AsyncClient, url: str, endpoint: str, uc_endpoint: str
    ) -> Dict[str, Any]:
        if not is_safe_url(url):
            return {
                "url": url,
                "title": "",
                "content": "",
                "raw_content": "",
                "error": "URL blocked by SSRF policy",
            }

        from tools.tool_backend_helpers import get_modal_auth_headers

        headers = {"Content-Type": "application/json"}
        headers.update(get_modal_auth_headers())

        # Try Primary Extractor: Crawl4AI
        try:
            resp = await client.post(
                endpoint,
                json={"url": url},
                headers=headers,
            )
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, dict) and data.get("status") == "success" and data.get("markdown"):
                    md_content = data.get("markdown", "")
                    return {
                        "url": url,
                        "title": data.get("title", ""),
                        "content": md_content,
                        "raw_content": md_content,
                        "metadata": {"source": "Crawl4AI Modal"},
                        "error": None,
                    }
        except Exception as e:
            logger.warning("Crawl4AI primary extraction failed for %s: %s. Trying UC Browser fallback...", url, e)

        # Fallback Extractor: SeleniumBase UC Mode Browser
        logger.info("Triggering SeleniumBase UC Browser fallback for: %s", url)
        try:
            resp = await client.post(
                uc_endpoint,
                json={"url": url},
                headers=headers,
                timeout=60.0,
            )
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, dict) and data.get("status") == "success":
                    snippet = data.get("snippet", "")
                    return {
                        "url": url,
                        "title": data.get("title", ""),
                        "content": snippet,
                        "raw_content": snippet,
                        "metadata": {"source": "SeleniumBase UC Browser Modal"},
                        "error": None,
                    }
                else:
                    err_msg = data.get("message", "Extraction failed") if isinstance(data, dict) else str(data)
                    return {
                        "url": url,
                        "title": "",
                        "content": "",
                        "raw_content": "",
                        "error": f"UC Browser extraction error: {err_msg}",
                    }
            else:
                return {
                    "url": url,
                    "title": "",
                    "content": "",
                    "raw_content": "",
                    "error": f"UC Browser returned HTTP {resp.status_code}",
                }
        except Exception as e:
            logger.error("UC Browser fallback failed for %s: %s", url, e)
            return {
                "url": url,
                "title": "",
                "content": "",
                "raw_content": "",
                "error": f"Web extraction failed on all backends: {str(e)}",
            }

