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
    min_containers=1,
    max_containers=1,
    timeout=86400,
)
@modal.web_server(
    port=GATEWAY_PORT,
    startup_timeout=120,
)
def api_server():
    """Run Hermes Trader Gateway server and Telegram polling loop."""
    hermes_volume.reload()
    env = build_runtime_environment()

    env["API_SERVER_ENABLED"] = "true"
    env["API_SERVER_PORT"] = str(GATEWAY_PORT)
    env["API_SERVER_HOST"] = "0.0.0.0"

    print("Starting Hermes Trader Gateway Server...")
    subprocess.run(
        ["hermes", "-p", "trader", "gateway", "run"],
        env=env,
        cwd=HERMES_ROOT,
        check=True,
    )
