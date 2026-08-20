# Workspace Rules for Hermes Modal Deployments & Provider Management

## Secrets and Provider Credentials

- Store every provider credential only in the Modal Secret `hermes-provider-keys`.
- Keep provider configuration files limited to `name`, `base_url`, `model`, `models`, and `key_env`; never store plaintext `api_key` values in `config.yaml`, `.env`, source code, runtime files, Git, or Modal Volumes.
- Use the matching environment names for active providers: `HCNSEC_API_KEY`, `IAMHC_API_KEY`, `LYCLAUDE_API_KEY`, `VYCEAI_API_KEY`, `NARAROUTER_API_KEY`, and `ZENMUX_API_KEY`.
- After a credential is exposed, rotate it in the provider dashboard and update the Modal Secret. Do not print, log, or inspect secret values; verify only that the variable is present.
- Deployment scripts must bind `modal.Secret.from_name("hermes-provider-keys")` and must not persist the process environment to `$HERMES_HOME/.env` or any Volume.
- Trader exchange credentials remain in the separate `hermes_trader_live` Secret and are injected only into the temporary live Freqtrade config; keep `dry_run=true` unless explicitly approved.

## Provider Addition/Removal Protocol
When adding or removing an inference provider for Hermes:
1. **Edit Config**: Always update the `custom_providers:` list directly inside `~/.hermes/config.yaml`. Do NOT hardcode provider logic into deployment scripts.
2. **Sync Modal Volume Profiles**: Sync `~/.hermes/config.yaml` to Modal volume for root and all active profiles:
   `modal volume put hermes-storage ~/.hermes/config.yaml /config.yaml --force`
   `modal volume put hermes-storage ~/.hermes/config.yaml /profiles/hazem/config.yaml --force`
   `modal volume put hermes-storage ~/.hermes/config.yaml /profiles/projectsentinelsupport/config.yaml --force`
   `modal volume put hermes-storage ~/.hermes/config.yaml /profiles/trader/config.yaml --force`
3. **Re-deploy Active Hermes Applications**: Deploy only the active Hermes instances to Modal in order:
   - **Hermes Personal**: `modal deploy modal_deploy.py` # personal assistant for the user (نشط)
   - **Hermes Trader**: `modal deploy modal_deploy_trader.py` # dedicated crypto trader bot (نشط)
   > ⛔ **تنبيه حاسم ومستمر:** **Hermes Support** (`modal_deploy_support.py`) و **Hermes Hazem** (`modal_deploy_hazem.py`) **معطلان صراحةً حالياً**، ويُحظر تماماً نشر أي منهما لتجنب تنشيطهما وإيقاظهما بالخطأ.
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
- **`hermes-trader`** (`modal_trader.py`):
  - Tool: `trader_status` (`tools/trader_tool.py`)
  - Endpoint: `https://hoysama--hermes-trader-status.modal.run`
  - Engine: Freqtrade OKX/Binance Spot Quantitative Trading Engine (Dry-Run & Live Trading, 5% dynamic stake per trade, $20 daily loss limit, Sharia-compliant 15-coin whitelist).
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
1. Deploy the specific microservice (e.g. `modal deploy modal_whisper.py`, `modal deploy modal_searxng.py`, or `modal deploy modal_trader.py`).
2. Verify the tool implementation in `tools/*.py` complies with `registry.register(handler=..., schema=...)`.
3. Sync non-secret `config.yaml` only to Modal volume profiles:
   `modal volume put hermes-storage ~/.hermes/config.yaml /config.yaml --force`
   `modal volume put hermes-storage ~/.hermes/config.yaml /profiles/hazem/config.yaml --force`
   `modal volume put hermes-storage ~/.hermes/config.yaml /profiles/projectsentinelsupport/config.yaml --force`
   `modal volume put hermes-storage ~/.hermes/config.yaml /profiles/trader/config.yaml --force`
   Never copy `.env` files or secret values to the Modal Volume. Secrets are
   injected by the bound Modal Secret at process startup.
4. Redeploy only active Hermes instances (`modal deploy modal_deploy.py` and `modal deploy modal_deploy_trader.py`) so the updated tool definitions and configs take effect in active gateway sessions. Never redeploy `modal_deploy_support.py` or `modal_deploy_hazem.py` as they are explicitly disabled.

### 4. Modal Proxy Auth Security & Secrets Management
- **Proxy Token Authorization**: All auxiliary microservices are secured with `requires_proxy_auth=True`. Tools in `tools/` and `plugins/` must send `get_modal_auth_headers()` from `tools/tool_backend_helpers.py`.
- **Runtime Environment Filter (`build_runtime_environment`)**: Deployment scripts (`modal_deploy.py`, `modal_deploy_hazem.py`, `modal_deploy_support.py`, `modal_deploy_trader.py`) explicitly preserve `MODAL_PROXY_TOKEN_*` environment variables when generating `$HERMES_HOME/.env`.
- **Modal Secrets Binding**: Cloud secrets (`hermes_trader`, `modal_proxy_tokens`, `searxng`) are listed in `hermes_secrets` across deployment scripts for automated cloud secret injection.

---

## Hermes Trader Modal Runbook

The Trader deployment is the Modal app `hermes-trader-gateway-server`.
Its deployment source is `modal_deploy_trader.py`; runtime scripts are mounted
from `hermes_trader/runtime/scripts/`. Persistent Freqtrade and Hermes state
lives on the `hermes-storage` volume. Never delete, replace, or reinitialize
that volume while diagnosing Trader behavior.

### Safety invariants

- Keep Freqtrade in `dry_run` unless the user explicitly authorizes live
  trading. Verify `dry_run: true` after every deploy or rollover.
- Never print `HCNSEC_API_KEY`, `NARAROUTER_API_KEY`, exchange keys, proxy
  tokens, or any other secret. Check only presence and length.
- Container IDs and Cron job IDs are runtime values. Discover them before use;
  never reuse an old ID from a previous deployment.
- Use `modal app rollover` only for refreshing runtime containers, such as
  loading a changed Modal Secret. Use `modal deploy` after source or image
  changes.
- Do not use destructive Modal or Git commands while investigating state.

### Discover the deployment

Run from the repository root and prefer the repository virtual environment:

```bash
venv/bin/modal container list
venv/bin/modal app list
```

Set the active Trader container ID returned by `container list`:

```bash
TRADER_CONTAINER="<current-ta-container-id>"
```

If `modal container exec` reports that the task is terminated, run
`venv/bin/modal container list` again and use the replacement Trader ID.

### Inspect Modal logs

Use the app name for logs because container IDs become stale after rollover,
preemption, or deployment:

```bash
venv/bin/modal app logs hermes-trader-gateway-server \
  --tail 500 --since 60m --timestamps
```

Useful focused diagnostics:

```bash
venv/bin/modal app logs hermes-trader-gateway-server \
  --tail 500 --since 60m --timestamps \
  --search 'Cycle mode|EXIT REVIEW|Provider:|deepseek|stepfun|agnes|LLM HTTP|429|503|timeout|dry_run|Cycle completed'
```

Interpret common errors as follows:

- `HTTP 429`: NaraRouter request-per-minute limit, not a missing API key.
- `HTTP 402`: provider quota, balance, or model-group allowance rejection.
- `HTTP 503`: upstream model service unavailable.
- `finish_reason=length` with empty content: invalid incomplete model output.
- `LLM unavailable`: a safety state; inspect the preceding provider error for
  the actual cause.
- `Container terminated due to preemption`: Modal restarted the container;
  verify that the persistent volume restored Freqtrade and Hermes state.

### Verify the deployed Trader

Inspect model selection and dry-run mode without exposing credentials:

```bash
venv/bin/modal container exec "$TRADER_CONTAINER" -- sh -lc \
  'grep -n "ANALYSIS_MODEL\|DECISION_MODEL\|deepseek-v4-flash-free\|dry_run" \
   /root/.hermes/profiles/trader/scripts/hermes_freqtrade_controller.py \
   /root/.hermes/profiles/trader/freqtrade/config/config.json'
```

Check secret injection safely:

```bash
venv/bin/modal container exec "$TRADER_CONTAINER" -- sh -lc \
  'python - <<"PY"
import os
for name in ("NARAROUTER_API_KEY", "HCNSEC_API_KEY"):
    value = os.environ.get(name, "")
    print(name, "present" if value else "missing", "length=" + str(len(value)))
PY'
```

The expected Freqtrade configuration contains:

```text
dry_run: true
```

`forceenter` and `forcesell` logs are simulated orders while dry-run is on;
they are not proof of real exchange execution.

### Read recent Trader reports

The no-agent Trader Cron script writes reports to:

```text
/root/.hermes/profiles/trader/cron_output.log
```

Extract a concise summary of the latest ten reports:

```bash
venv/bin/modal container exec "$TRADER_CONTAINER" -- sh -lc \
  'python - <<"PY"
import re
text = open("/root/.hermes/profiles/trader/cron_output.log", errors="replace").read()
reports = text.split("🤖 Hermes Trading Report | ")[1:]
for report in reports[-10:]:
    lines = report.splitlines()
    timestamp = lines[0] if lines else "?"
    decisions = next((x.strip() for x in lines if re.match(r"BUY: \\d+ \\| SELL:", x.strip())), "-")
    execution = next((x.strip() for x in lines if x.startswith("نتيجة التنفيذ:")), "-")
    opened = next((x.strip() for x in lines if x.startswith("📦 عدد الصفقات")), "-")
    reason = next((x.strip() for x in lines if x.startswith("⛔ سبب منع")), "-")
    print(f"{timestamp} | {decisions} | {execution} | {opened} | {reason}")
PY'
```

Read the latest persisted state and its timestamp:

```bash
venv/bin/modal container exec "$TRADER_CONTAINER" -- sh -lc \
  'python - <<"PY"
import json
p = "/root/.hermes/profiles/trader/freqtrade/hermes_state.json"
state = json.load(open(p))
print("last_update:", state.get("last_update"))
print("last_regime:", state.get("last_regime"))
print("decision_count:", len(state.get("last_decisions", {})))
PY'
```

Do not treat `last_decisions` as a fresh exit-review result. It may contain the
last entry-cycle decisions. Entry and exit reporting should be interpreted as
separate streams:

```text
Entry: BUY / SELL / NEUTRAL
Exit: HOLD / SELL candidates / SELL confirmed / SELL executed
```

### Inspect and edit Trader Cron

List the profile's jobs and discover the current job ID:

```bash
venv/bin/modal container exec "$TRADER_CONTAINER" -- sh -lc \
  'hermes -p trader cron list'
```

The main job is normally named `Hermes AI Trading Cycle`, runs
`run_trading_cycle.sh`, and uses no-agent mode. Edit its schedule only after
confirming the ID from `cron list`:

```bash
venv/bin/modal container exec "$TRADER_CONTAINER" -- sh -lc \
  'hermes -p trader cron edit <job-id> --schedule "*/3 * * * *"'
```

The current intended schedule is:

- Entry analysis: every 20 minutes.
- Open-position exit review: every 3 minutes.

The outer Cron schedule and the script's mode selection must agree. Do not
assume `minute % 20 == 0` produces exact 20-minute entry cycles when the outer
job runs every 3 minutes; use a persisted last-entry timestamp or separate
Cron jobs if exact cadence is required.

Run a job manually only for diagnosis and only after checking that no cycle is
already running:

```bash
venv/bin/modal container exec "$TRADER_CONTAINER" -- sh -lc \
  'hermes -p trader cron run <job-id>'
```

### Deploy and refresh

After changing Trader source or image configuration:

```bash
venv/bin/modal deploy modal_deploy_trader.py
```

After changing only a Modal Secret:

```bash
venv/bin/modal app rollover hermes-trader-gateway-server
```

After either operation:

1. Run `venv/bin/modal container list` and identify the new Trader container.
2. Verify the deployed models and `dry_run: true` with `modal container exec`.
3. Inspect startup logs and the first completed cycle.
4. Confirm the Freqtrade database and `hermes_state.json` still exist.

Do not deploy merely to change a Cron schedule; use `hermes cron edit`.
Do not use rollover as a substitute for deploying source changes.

### Provider and rate-limit rules

Current Trader roles:

```text
deepseek-v4-flash-free: market analysis
agnes-2.5-flash: pair decisions and confirmation
```

NaraRouter's observed free-plan limit is `10 requests/min`, separate from the
daily token allowances. Do not fan out one request per pair or per open
position. Prefer one batched request for all entry pairs and one batched
request for all open positions during exit review.

On `429`, respect `Retry-After` when present and defer work to a later cycle;
do not immediately retry every failed pair. On provider failure, block new
entries while keeping local `stop_loss`, `take_profit`, and `time_stop` rules
active. All Trader execution remains `dry_run` unless the user explicitly
authorizes a live-trading change.
