import os
import subprocess
import modal
APP_NAME = "hermes-support-api-server"
HERMES_ROOT = "/workspace/hermes-agent"
HERMES_HOME = "/root/.hermes"

GATEWAY_PORT = 8642

app = modal.App(APP_NAME)

hermes_volume = modal.Volume.from_name(
    "hermes-storage",
    create_if_missing=True,
)

hermes_secrets = [
    modal.Secret.from_name("hermes-secrets"),
    modal.Secret.from_name("telegram-support"),
    modal.Secret.from_name("cloudflare"),
    modal.Secret.from_name("codexeverywhere"),
    modal.Secret.from_name("github-secret"),
    modal.Secret.from_name("Futureopensource"),
    modal.Secret.from_name("iamhc"),
]

# صورة Hermes المجهزة بـ Bun و Node.js و gh و wrangler
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
    .run_commands(
        "curl -fsSL https://deb.nodesource.com/setup_24.x | bash -",
        "apt-get install -y nodejs",
        "curl -fsSL https://bun.sh/install | bash",
        "ln -s /root/.bun/bin/bun /usr/local/bin/bun",
        "curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg | dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg",
        'echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" | tee /etc/apt/sources.list.d/github-cli.list > /dev/null',
        "apt-get update && apt-get install -y gh",
        "bun add -g wrangler",
        "ln -s /root/.bun/bin/wrangler /usr/local/bin/wrangler",
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
    """Build the runtime environment and persist selected variables."""
    env = os.environ.copy()
    env["HERMES_HOME"] = HERMES_HOME

    import secrets
    if not env.get("API_SERVER_KEY") or len(env.get("API_SERVER_KEY", "")) < 16:
        env["API_SERVER_KEY"] = secrets.token_hex(32)

    os.makedirs(HERMES_HOME, exist_ok=True)

    env_path = os.path.join(HERMES_HOME, ".env")
    temporary_path = f"{env_path}.tmp.{os.getpid()}"

    excluded_names = {
        "PATH",
        "PWD",
        "HOME",
        "HOSTNAME",
        "SHLVL",
        "_",
    }

    with open(temporary_path, "w", encoding="utf-8") as env_file:
        os.chmod(temporary_path, 0o600)

        for name, value in sorted(env.items()):
            if name.startswith("MODAL_") or name in excluded_names:
                continue

            normalized_value = value.replace("\r", "\\r").replace("\n", "\\n")
            env_file.write(f"{name}={normalized_value}\n")

        env_file.flush()
        os.fsync(env_file.fileno())

    os.replace(temporary_path, env_path)
    os.chmod(env_path, 0o600)

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
    """Run the Hermes messaging gateway and API server."""
    import os
    import subprocess
    
    # Reload the volume to get latest config if the container was reused
    hermes_volume.reload()
    
    env = build_runtime_environment()

    env["API_SERVER_ENABLED"] = "true"
    env["API_SERVER_PORT"] = str(GATEWAY_PORT)
    env["API_SERVER_HOST"] = "0.0.0.0"
    env.setdefault("TELEGRAM_ALLOWED_USERS", "*")

    subprocess.run(
        ["hermes", "-p", "projectsentinelsupport", "gateway", "run"],
        env=env,
        cwd=HERMES_ROOT,
        check=True,
    )
