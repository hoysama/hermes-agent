import os
import subprocess
import modal

APP_NAME = "hermes-trader-gateway-server"
HERMES_ROOT = "/workspace/hermes-agent"
HERMES_HOME = "/root/.hermes"

GATEWAY_PORT = 8645

app = modal.App(APP_NAME)

hermes_volume = modal.Volume.from_name(
    "hermes-storage",
    create_if_missing=True,
)

hermes_secrets = [
    modal.Secret.from_name("hermes_trader"),
    modal.Secret.from_name("hermes-secrets"),
    modal.Secret.from_name("modal_proxy_tokens"),
    modal.Secret.from_name("searxng"),
]

hermes_image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install(
        "git",
        "curl",
        "build-essential",
        "ca-certificates",
        "unzip",
        "gnupg",
    )
    .add_local_dir(
        ".",
        remote_path=HERMES_ROOT,
        copy=True,
        ignore=[
            ".git",
            "node_modules",
            "web/node_modules",
            ".venv",
            "venv",
            "__pycache__",
        ],
    )
    .run_commands(
        f"pip install -e {HERMES_ROOT}",
    )
)


def build_runtime_environment() -> dict[str, str]:
    """Build the runtime environment for Hermes Trader Gateway."""
    env = os.environ.copy()
    env["HERMES_HOME"] = HERMES_HOME
    env["TERMINAL_CWD"] = f"{HERMES_HOME}/workspaces"
    env["HERMES_AGENT_TIMEOUT_WARNING"] = "3600"
    env["HERMES_AGENT_TIMEOUT"] = "7200"

    os.makedirs(HERMES_HOME, exist_ok=True)
    os.makedirs(os.path.join(HERMES_HOME, "workspaces"), exist_ok=True)
    os.makedirs(os.path.join(HERMES_HOME, "profiles", "trader"), exist_ok=True)

    # Persist TELEGRAM_BOT_TOKEN from modal secret to profile .env
    trader_env_path = os.path.join(HERMES_HOME, "profiles", "trader", ".env")
    token = env.get("TELEGRAM_BOT_TOKEN", "")
    with open(trader_env_path, "w") as f:
        f.write(f'TELEGRAM_BOT_TOKEN="{token}"\n')

    # Also persist to root .env
    root_env_path = os.path.join(HERMES_HOME, ".env")
    with open(root_env_path, "w") as f:
        f.write(f'TELEGRAM_BOT_TOKEN="{token}"\n')

    return env


@app.function(
    image=hermes_image,
    volumes={HERMES_HOME: hermes_volume},
    secrets=hermes_secrets,
    max_containers=1,
    timeout=86400,
)
def run_gateway():
    """Run Hermes Trader Gateway process."""
    hermes_volume.reload()
    env = build_runtime_environment()
    hermes_volume.commit()

    print("Starting Hermes Trader Gateway Server...")
    process = subprocess.Popen(
        ["hermes", "-p", "trader", "gateway", "run"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    for line in iter(process.stdout.readline, ""):
        print(line, end="")

    process.wait()
    if process.returncode != 0:
        raise subprocess.CalledProcessError(process.returncode, process.args)


@app.function(
    image=hermes_image,
    volumes={HERMES_HOME: hermes_volume},
    secrets=hermes_secrets,
    timeout=86400,
)
@modal.asgi_app()
def api_server():
    """Expose Hermes Trader Gateway API server."""
    from fastapi import FastAPI, Response
    from fastapi.middleware.cors import CORSMiddleware
    import urllib.request
    import urllib.error

    fastapi_app = FastAPI(title="Hermes Trader API Server")
    fastapi_app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @fastapi_app.get("/health")
    def health():
        return {"status": "ok", "app": APP_NAME}

    @fastapi_app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"])
    def proxy(path: str, response: Response):
        url = f"http://127.0.0.1:{GATEWAY_PORT}/{path}"
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req) as resp:
                response.status_code = resp.status
                return resp.read()
        except urllib.error.HTTPError as e:
            response.status_code = e.code
            return e.read()
        except Exception:
            response.status_code = 502
            return {"error": "Gateway proxy unreachable"}

    return fastapi_app
