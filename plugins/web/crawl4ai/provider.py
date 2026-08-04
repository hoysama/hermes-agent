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


class Crawl4AIWebSearchProvider(WebSearchProvider):
    """Web extraction provider using Crawl4AI Modal endpoint."""

    @property
    def name(self) -> str:
        return "crawl4ai"

    @property
    def display_name(self) -> str:
        return "Crawl4AI (Modal Cloud)"

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
        """Extract content from multiple URLs concurrently via Crawl4AI Modal Endpoint."""
        endpoint = os.getenv("CRAWL4AI_MODAL_URL", DEFAULT_CRAWL4AI_ENDPOINT)
        
        async with httpx.AsyncClient(timeout=60.0) as client:
            tasks = [self._extract_single(client, url, endpoint) for url in urls]
            results = await asyncio.gather(*tasks)
            
        return list(results)

    async def _extract_single(
        self, client: httpx.AsyncClient, url: str, endpoint: str
    ) -> Dict[str, Any]:
        if not is_safe_url(url):
            return {
                "url": url,
                "title": "",
                "content": "",
                "raw_content": "",
                "error": "URL blocked by SSRF policy",
            }

        try:
            resp = await client.post(
                endpoint,
                json={"url": url},
                headers={"Content-Type": "application/json"},
            )
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, dict) and data.get("status") == "success":
                    md_content = data.get("markdown", "")
                    return {
                        "url": url,
                        "title": data.get("title", ""),
                        "content": md_content,
                        "raw_content": md_content,
                        "metadata": {"source": "Crawl4AI Modal"},
                        "error": None,
                    }
                else:
                    err_msg = data.get("message", "Extraction failed") if isinstance(data, dict) else str(data)
                    return {
                        "url": url,
                        "title": "",
                        "content": "",
                        "raw_content": "",
                        "error": f"Crawl4AI extraction error: {err_msg}",
                    }
            else:
                return {
                    "url": url,
                    "title": "",
                    "content": "",
                    "raw_content": "",
                    "error": f"Crawl4AI returned HTTP {resp.status_code}",
                }
        except Exception as e:
            logger.warning("Crawl4AI extraction error for %s: %s", url, e)
            return {
                "url": url,
                "title": "",
                "content": "",
                "raw_content": "",
                "error": f"Crawl4AI request failed: {str(e)}",
            }

