"""Modal App: hermes-searxng

Private, serverless multi-engine web search API service for Hermes agents.
Provides a fast, zero-cost, clean JSON search endpoint compliant with SearXNG schema.

Endpoint:
  GET/POST /search?q=<query>&format=json
"""

import modal
from typing import Dict, Any, List

app = modal.App("hermes-searxng")

# Define lightweight image with web search & API dependencies
image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install(
        "duckduckgo_search>=6.0.0",
        "httpx>=0.27.0",
        "fastapi>=0.110.0",
        "pydantic>=2.0.0",
    )
)


@app.function(
    image=image,
    cpu=1.0,
    memory=512,
    scaledown_window=60,
)
@modal.web_endpoint(method="GET")
def search(q: str = "", format: str = "json", max_results: int = 8) -> Dict[str, Any]:
    """Web search endpoint compatible with SearXNG JSON schema."""
    if not q or not q.strip():
        return {
            "query": "",
            "number_of_results": 0,
            "results": [],
            "error": "Query string 'q' parameter is required.",
        }

    query_str = q.strip()
    results: List[Dict[str, str]] = []

    try:
        from duckduckgo_search import DDGS

        with DDGS() as ddgs:
            raw_results = list(ddgs.text(query_str, max_results=max_results))
            for item in raw_results:
                results.append({
                    "title": item.get("title", ""),
                    "url": item.get("href", "") or item.get("link", ""),
                    "content": item.get("body", "") or item.get("snippet", ""),
                    "engine": "duckduckgo",
                })
    except Exception as exc:
        # Fallback to direct HTTPX query if DDGS package hits temporary issue
        try:
            import httpx
            resp = httpx.get(
                "https://html.duckduckgo.com/html/",
                params={"q": query_str},
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
                timeout=10,
            )
            if resp.status_code == 200:
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(resp.text, "html.parser")
                for a in soup.select(".result__body")[:max_results]:
                    title_elem = a.select_one(".result__title")
                    snippet_elem = a.select_one(".result__snippet")
                    url_elem = a.select_one(".result__url")
                    if title_elem:
                        results.append({
                            "title": title_elem.get_text(strip=True),
                            "url": url_elem.get_text(strip=True) if url_elem else "",
                            "content": snippet_elem.get_text(strip=True) if snippet_elem else "",
                            "engine": "duckduckgo-html",
                        })
        except Exception as inner_exc:
            return {
                "query": query_str,
                "number_of_results": 0,
                "results": [],
                "error": f"Search failed: {exc} | Fallback failed: {inner_exc}",
            }

    return {
        "query": query_str,
        "number_of_results": len(results),
        "results": results,
    }


@app.function(image=image)
@modal.web_endpoint(method="GET")
def health() -> Dict[str, Any]:
    """Health check endpoint."""
    return {"status": "ok", "app": "hermes-searxng"}
