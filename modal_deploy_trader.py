import os
import json
import shutil
import subprocess
import yaml
import modal

APP_NAME = "hermes-trader-gateway-server"
HERMES_ROOT = "/workspace/hermes-agent"
HERMES_HOME = "/root/.hermes"

GATEWAY_PORT = 8645
FREQTRADE_CONFIG = f"{HERMES_HOME}/profiles/trader/freqtrade/config/config.json"
FREQTRADE_STRATEGY_PATH = f"{HERMES_HOME}/profiles/trader/freqtrade/user_data/strategies"
FREQTRADE_USERDIR = f"{HERMES_HOME}/profiles/trader/freqtrade/user_data"
FREQTRADE_RUNTIME_CONFIG = "/tmp/hermes-freqtrade-config.json"

TRADER_RUNTIME_SOURCE = "/opt/hermes-trader-runtime"
TRADER_SCRIPTS = os.path.join(HERMES_HOME, "profiles", "trader", "scripts")
TRADER_STRATEGIES = os.path.join(FREQTRADE_USERDIR, "strategies")
CUSTOM_PROVIDER_ENV = {
    "iamhc": "IAMHC_API_KEY",
    "hcnsec": "HCNSEC_API_KEY",
    "lyclaude": "LYCLAUDE_API_KEY",
    "vyceai": "VYCEAI_API_KEY",
    "nararouter": "NARAROUTER_API_KEY",
    "modernrouter": "MODERNROUTER_API_KEY",
    "zenmux": "ZENMUX_API_KEY",
}

app = modal.App(APP_NAME)

hermes_volume = modal.Volume.from_name(
    "hermes-storage",
    create_if_missing=True,
)

hermes_secrets = [
    modal.Secret.from_name("hermes_trader"),
    modal.Secret.from_name("hermes_trader_live"),
    modal.Secret.from_name("hermes-provider-keys"),
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
    .add_local_dir(
        "hermes_trader/runtime",
        remote_path=TRADER_RUNTIME_SOURCE,
        copy=True,
    )
    .run_commands(
        # Freqtrade must be part of the immutable image. Runtime state,
        # configs, databases, strategies, and reports live in hermes_volume.
        f"pip install -e {HERMES_ROOT} ccxt freqtrade",
    )
)


def build_runtime_environment() -> dict[str, str]:
    """Build the runtime environment for Hermes Trader Gateway."""
    env = os.environ.copy()
    env["HERMES_HOME"] = HERMES_HOME
    env["TERMINAL_CWD"] = f"{HERMES_HOME}/workspaces"
    env["HERMES_AGENT_TIMEOUT_WARNING"] = "3600"
    env["HERMES_AGENT_TIMEOUT"] = "7200"
    # The controller reads credentials only from Modal Secrets, never source
    # files or the persistent Volume.
    env.setdefault("FREQTRADE_API_USERNAME", "hermes")
    env.setdefault("FREQTRADE_API_PASSWORD", "hermes123")
    env.setdefault("HCNSEC_BASE_URL", "https://api.hcnsec.cn/v1")

    os.makedirs(HERMES_HOME, exist_ok=True)
    os.makedirs(os.path.join(HERMES_HOME, "workspaces"), exist_ok=True)
    os.makedirs(os.path.join(HERMES_HOME, "profiles", "trader"), exist_ok=True)

    config_path = os.path.join(HERMES_HOME, "profiles", "trader", "config.yaml")
    if os.path.isfile(config_path):
        with open(config_path, encoding="utf-8") as handle:
            config = yaml.safe_load(handle) or {}
        changed = False
        for provider in config.get("custom_providers", []) or []:
            if not isinstance(provider, dict):
                continue
            name = str(provider.get("name", "")).strip().lower()
            env_name = CUSTOM_PROVIDER_ENV.get(name)
            if env_name and provider.get("api_key"):
                provider.pop("api_key", None)
                provider["key_env"] = env_name
                changed = True
        if changed:
            temporary_path = f"{config_path}.tmp.{os.getpid()}"
            with open(temporary_path, "w", encoding="utf-8") as handle:
                os.chmod(temporary_path, 0o600)
                yaml.safe_dump(config, handle, sort_keys=False, allow_unicode=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, config_path)
            os.chmod(config_path, 0o600)

    return env


def build_freqtrade_runtime_config(env: dict[str, str]) -> str:
    """Create a non-persistent Freqtrade config with live credentials injected.

    The persistent config never contains exchange credentials. Dry-run keeps
    them absent; live mode requires all three OKX values from Modal Secrets.
    """
    with open(FREQTRADE_CONFIG, encoding="utf-8") as handle:
        config = json.load(handle)

    exchange = config.setdefault("exchange", {})
    if config.get("dry_run", True):
        exchange.pop("key", None)
        exchange.pop("secret", None)
        exchange.pop("password", None)
    else:
        required = ("OKX_API_KEY", "OKX_SECRET_KEY", "OKX_PASSPHRASE")
        missing = [name for name in required if not env.get(name)]
        if missing:
            raise RuntimeError(
                "Live trading blocked; missing Modal Secret values: "
                + ", ".join(missing)
            )
        exchange["key"] = env["OKX_API_KEY"]
        exchange["secret"] = env["OKX_SECRET_KEY"]
        exchange["password"] = env["OKX_PASSPHRASE"]

    with open(FREQTRADE_RUNTIME_CONFIG, "w", encoding="utf-8") as handle:
        json.dump(config, handle, indent=2)
    return FREQTRADE_RUNTIME_CONFIG


def sync_trader_code() -> None:
    """Synchronize versioned Trader code without replacing runtime state."""
    os.makedirs(TRADER_SCRIPTS, exist_ok=True)
    os.makedirs(TRADER_STRATEGIES, exist_ok=True)

    for name in os.listdir(os.path.join(TRADER_RUNTIME_SOURCE, "scripts")):
        source = os.path.join(TRADER_RUNTIME_SOURCE, "scripts", name)
        if os.path.isfile(source):
            shutil.copy2(source, os.path.join(TRADER_SCRIPTS, name))

    for name in os.listdir(os.path.join(TRADER_RUNTIME_SOURCE, "strategies")):
        source = os.path.join(TRADER_RUNTIME_SOURCE, "strategies", name)
        if os.path.isfile(source):
            shutil.copy2(source, os.path.join(TRADER_STRATEGIES, name))

    # The persistent config owns user/runtime state. Seed it only when absent;
    # credentials are still injected into the temporary config below.
    if not os.path.isfile(FREQTRADE_CONFIG):
        shutil.copy2(
            os.path.join(TRADER_RUNTIME_SOURCE, "config.json"), FREQTRADE_CONFIG
        )


@app.function(
    image=hermes_image,
    volumes={HERMES_HOME: hermes_volume},
    secrets=hermes_secrets,
    min_containers=1,
    max_containers=1,
    # Hermes, Freqtrade, CCXT websockets, and the gateway share this long-lived
    # container. Keep a modest reservation above Modal's 128 MiB default so the
    # always-on dry-run workload remains schedulable without over-reserving.
    cpu=0.25,
    memory=512,
    timeout=86400,
)
@modal.web_server(
    port=GATEWAY_PORT,
    startup_timeout=120,
)
def api_server():
    """Run Freqtrade and the Hermes Trader Gateway in one container."""
    hermes_volume.reload()
    env = build_runtime_environment()
    sync_trader_code()

    if not os.path.isfile(FREQTRADE_CONFIG):
        raise RuntimeError(f"Missing persistent Freqtrade config: {FREQTRADE_CONFIG}")
    runtime_config = build_freqtrade_runtime_config(env)

    env["API_SERVER_ENABLED"] = "true"
    env["API_SERVER_PORT"] = str(GATEWAY_PORT)
    env["API_SERVER_HOST"] = "0.0.0.0"
    # The Hermes API adapter refuses to bind without a strong caller key.
    # Reuse the already-injected Modal proxy secret instead of putting a
    # credential in the image or persistent volume.
    api_key = env.get("MODAL_PROXY_TOKEN_SECRET", "")
    if api_key:
        env["API_SERVER_KEY"] = api_key

    freqtrade = subprocess.Popen(
        [
            "freqtrade",
            "trade",
            "--config",
            runtime_config,
            "--userdir",
            FREQTRADE_USERDIR,
            "--strategy",
            "HermesExecutionStrategy",
            "--strategy-path",
            FREQTRADE_STRATEGY_PATH,
        ],
        env=env,
        cwd=HERMES_ROOT,
    )
    print(f"Started Freqtrade (pid={freqtrade.pid}) using persistent config")
    try:
        print("Starting Hermes Trader Gateway Server...")
        subprocess.run(
            ["hermes", "-p", "trader", "gateway", "run"],
            env=env,
            cwd=HERMES_ROOT,
            check=True,
        )
    finally:
        if freqtrade.poll() is None:
            freqtrade.terminate()
            freqtrade.wait(timeout=30)
