# Workspace Rules for Hermes Modal Deployments & Provider Management

## Provider Addition/Removal Protocol
When adding or removing an inference provider for Hermes:
1. **Edit Config**: Always update the `custom_providers:` list directly inside `~/.hermes/config.yaml`. Do NOT hardcode provider logic into deployment scripts.
2. **Sync Modal Volume Profiles**: Sync `~/.hermes/config.yaml` to Modal volume for root and all active profiles:
   `modal volume put hermes-storage ~/.hermes/config.yaml /config.yaml --force`
   `modal volume put hermes-storage ~/.hermes/config.yaml /profiles/hazem/config.yaml --force`
   `modal volume put hermes-storage ~/.hermes/config.yaml /profiles/projectsentinelsupport/config.yaml --force`
3. **Re-deploy All 3 Hermes Applications**: Deploy all three Hermes instances to Modal in order:
   - **Hermes Personal**: `modal deploy modal_deploy.py` # personal assistant for the user
   - **Hermes Support**: `modal deploy modal_deploy_support.py`
   - **Hermes Hazem**: `modal deploy modal_deploy_hazem.py`
4. **Verify Provider Registration**: Ensure the new provider appears in the list of available providers by running `hermes providers list` or checking the Modal dashboard.

---

## Tool Registration & Modal Microservices Protocol

### 1. Tool Registration Standard in Hermes Core
When registering custom tools in `tools/*.py`:
- **Must use `handler=` and `schema=`**: Always pass `handler=` and `schema=` to `registry.register()`. Do NOT use `func=` (which will cause a registration exception).
- **Schema Format**: The schema must follow OpenAI function calling format (`name`, `description`, `parameters`).
- **Example**:
  ```python
  registry.register(
      name="web_rerank",
      toolset="web",
      schema=WEB_RERANK_SCHEMA,
      handler=lambda args, **kw: web_rerank_tool(
          query=args.get("query", ""),
          passages=args.get("passages", []),
          top_n=args.get("top_n", 5),
      ),
      emoji="🧠",
  )
  ```

### 2. Deployed Modal Auxiliary Microservices
Hermes connects to dedicated Modal microservices to offload heavy workloads:
- **`hermes-whisper`** (`modal_whisper.py`):
  - Tool: `audio_transcribe` (`tools/audio_transcribe_tool.py`) & Gateway Auto-STT Provider (`stt.provider: modal` in `tools/transcription_tools.py`)
  - Endpoint: `https://hoysama--hermes-whisper-transcribe.modal.run`
  - Engine: Faster-Whisper (`large-v3`) on GPU with CUDA 12 preloading (`nvidia-cublas-cu12`, `nvidia-cudnn-cu12`), VAD filtering & auto language detection.
- **`hermes-searxng`** (`modal_searxng.py`):
  - Tool: `web_search` (`tools/web_search_tool.py` via `web.search_backend: searxng`)
  - Endpoint: `https://hoysama--hermes-searxng-search.modal.run`
  - Engine: Multi-engine web search (SearXNG + DuckDuckGo + Google) at zero API cost.
- **`hermes-reranker`** (`modal_reranker.py`):
  - Tool: `web_rerank` (`tools/web_rerank_tool.py`)
  - Endpoint: `https://hoysama--hermes-reranker-rerank.modal.run`
  - Engine: FlashRank (`ms-marco-TinyBERT-L-2-v2`) cross-encoder for semantic passage ranking.
- **`hermes-docling`** (`modal_docling.py`):
  - Tool: `document_extract` (`tools/document_extract_tool.py`)
  - Endpoint: `https://hoysama--hermes-docling-extract-document.modal.run`
  - Engine: IBM Docling for deep PDF/DOCX/HTML layout extraction & OCR.
- **`hermes-web-extractor`** (`modal_extract.py`):
  - Tool: `web_extract` (`tools/web_extract_tool.py`)
  - Endpoint: `https://hoysama--hermes-web-extractor-extract.modal.run`
  - Engine: Crawl4AI + Playwright headless browser markdown web page extraction.
- **`hermes-uc-backend`** (`modal_uc_browser.py`):
  - Tool: Browser / Computer Use automation endpoint.
  - Endpoint: `https://hoysama--hermes-uc-backend-browser.modal.run`
  - Engine: Undetected-Chromium + Playwright browser automation.


### 3. Microservice Update & Deployment Workflow
When modifying any tool or backend microservice:
1. Deploy the specific microservice (e.g. `modal deploy modal_whisper.py` or `modal deploy modal_searxng.py`).
2. Verify the tool implementation in `tools/*.py` complies with `registry.register(handler=..., schema=...)`.
3. Sync `config.yaml` to Modal volume profiles:
   `modal volume put hermes-storage ~/.hermes/config.yaml /config.yaml --force`
   `modal volume put hermes-storage ~/.hermes/config.yaml /profiles/hazem/config.yaml --force`
   `modal volume put hermes-storage ~/.hermes/config.yaml /profiles/projectsentinelsupport/config.yaml --force`
4. Redeploy the 3 main Hermes instances (`modal_deploy.py`, `modal_deploy_support.py`, `modal_deploy_hazem.py`) so the updated tool definitions and configs take effect in active gateway sessions.