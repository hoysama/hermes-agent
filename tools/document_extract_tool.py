#!/usr/bin/env python3
"""Document Extraction Tool for Hermes

Connects Hermes to the Modal Document Intelligence Service (hermes-docling)
to extract clean Markdown text and structured tables from PDF, DOCX, PPTX, and XLSX files.
"""

import json
import logging
import requests
from tools.registry import registry, tool_error

logger = logging.getLogger(__name__)

MODAL_DOCLING_URL = "https://hoysama--hermes-docling-process-document.modal.run"


def document_extract_tool(url: str) -> str:
    """Send document URL to Modal Document Intelligence Service and return markdown."""
    url = (url or "").strip()
    if not url:
        return tool_error("url parameter is required.")

    try:
        response = requests.post(
            MODAL_DOCLING_URL,
            json={"url": url},
            timeout=120,
        )
        if response.status_code != 200:
            return tool_error(f"Document extraction failed with status {response.status_code}: {response.text}")

        data = response.json()
        if data.get("status") == "error":
            return tool_error(f"Document extraction error: {data.get('message')}")

        return json.dumps(
            {
                "success": True,
                "url": url,
                "markdown": data.get("markdown", ""),
                "markdown_length": data.get("markdown_length", 0),
            },
            ensure_ascii=False,
        )
    except Exception as exc:
        logger.error(f"Error calling Modal Document Extraction endpoint: {exc}")
        return tool_error(f"Failed to connect to document extraction service: {exc}")


DOCUMENT_EXTRACT_SCHEMA = {
    "name": "document_extract",
    "description": (
        "Extract clean Markdown text, structure, and tables from PDF documents, Office files "
        "(DOCX, PPTX, XLSX), or document URLs. Use this tool whenever you receive a link to a "
        "PDF file or document and need to read its full content, tables, or sections accurately."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "The URL of the PDF or Office document to process and extract.",
            },
        },
        "required": ["url"],
    },
}


registry.register(
    name="document_extract",
    toolset="web",
    schema=DOCUMENT_EXTRACT_SCHEMA,
    handler=lambda args, **kw: document_extract_tool(url=args.get("url", "")),
    emoji="📄",
)
