#!/usr/bin/env python3
"""Web Rerank Tool for Hermes

Connects Hermes to the Modal Reranker Service (hermes-reranker)
to filter and rank search snippets/passages by semantic relevance.
"""

import json
import logging
import requests
from tools.registry import registry, tool_error

logger = logging.getLogger(__name__)

MODAL_RERANKER_URL = "https://hoysama--hermes-reranker-rerank.modal.run"


def web_rerank_tool(query: str, passages: list, top_n: int = 5) -> str:
    """Rerank a list of passages or search snippets based on query relevance using CrossEncoder on Modal."""
    query = (query or "").strip()
    if not query:
        return tool_error("query parameter is required.")
    if not passages or not isinstance(passages, list):
        return tool_error("passages parameter must be a non-empty list of text strings.")

    try:
        response = requests.post(
            MODAL_RERANKER_URL,
            json={
                "query": query,
                "passages": passages,
                "top_n": top_n,
            },
            timeout=30,
        )
        if response.status_code != 200:
            return tool_error(f"Reranking failed with status {response.status_code}: {response.text}")

        data = response.json()
        if data.get("status") == "error":
            return tool_error(f"Reranker error: {data.get('message')}")

        return json.dumps(
            {
                "success": True,
                "query": query,
                "reranked_passages": data.get("reranked_passages", []),
            },
            ensure_ascii=False,
        )
    except Exception as exc:
        logger.error(f"Error calling Modal Reranker endpoint: {exc}")
        return tool_error(f"Failed to connect to reranker service: {exc}")


WEB_RERANK_SCHEMA = {
    "type": "function",
    "function": {
        "name": "web_rerank",
        "description": "Rank and filter a list of text snippets or search results by their exact semantic relevance to a query. Use this tool after web search when you have multiple search results and want to extract the top most relevant passages.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The target search query or question to evaluate relevance against.",
                },
                "passages": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of text snippets, search result summaries, or document paragraphs to rank.",
                },
                "top_n": {
                    "type": "integer",
                    "description": "Number of top relevant passages to return (default: 5).",
                    "default": 5,
                },
            },
            "required": ["query", "passages"],
        },
    },
}

registry.register(
    name="web_rerank",
    func=web_rerank_tool,
    schema=WEB_RERANK_SCHEMA,
    description="Rank search snippets or text passages by semantic relevance to a query via Modal CrossEncoder.",
)
