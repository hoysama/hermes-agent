import os
import tempfile
import modal

APP_NAME = "hermes-docling"

app = modal.App(APP_NAME)

doc_image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("git", "curl", "wget")
    .pip_install(
        "pymupdf4llm",
        "pymupdf",
        "markitdown",
        "fastapi",
        "uvicorn",
        "pydantic",
        "requests",
    )
)

@app.function(
    image=doc_image,
    timeout=180,
    min_containers=0,
)
@modal.fastapi_endpoint(method="POST", requires_proxy_auth=True)
async def process_document(data: dict):
    """
    Process PDF, Office files (DOCX, PPTX, XLSX), or HTML and convert them into clean Markdown.
    
    Payload: {"url": "https://example.com/document.pdf"}
    """
    url = data.get("url")
    if not url:
        return {"status": "error", "message": "Missing 'url' parameter"}, 400

    try:
        import requests

        response = requests.get(url, timeout=30)
        response.raise_for_status()

        ext = os.path.splitext(url.split("?")[0])[1].lower() or ".pdf"
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            tmp.write(response.content)
            tmp_path = tmp.name

        markdown_text = ""
        
        if ext == ".pdf":
            import pymupdf4llm
            markdown_text = pymupdf4llm.to_markdown(tmp_path)
        else:
            from markitdown import MarkItDown
            md = MarkItDown()
            result = md.convert(tmp_path)
            markdown_text = result.text_content

        try:
            os.remove(tmp_path)
        except Exception:
            pass

        return {
            "status": "success",
            "url": url,
            "markdown": markdown_text,
            "markdown_length": len(markdown_text or ""),
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}, 500
