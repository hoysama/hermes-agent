import os
import subprocess
import modal

APP_NAME = "hermes-api-server"
HERMES_ROOT = "/workspace/hermes-agent"
HERMES_HOME = "/root/.hermes"

GATEWAY_PORT = 8642
DASHBOARD_PORT = 9119

app = modal.App(APP_NAME)

hermes_volume = modal.Volume.from_name(
    "hermes-storage",
    create_if_missing=True,
)

hermes_secrets = [
    modal.Secret.from_name("hermes-secrets"),
    modal.Secret.from_name("telegram"),
    modal.Secret.from_name("Providerscloudflare"),
    modal.Secret.from_name("cloudflare"),
    modal.Secret.from_name("codexeverywhere"),
    modal.Secret.from_name("github-secret"),
    modal.Secret.from_name("Futureopensource"),
    modal.Secret.from_name("iamhc"),
    # السكرت الخاص بالمصادقة للوحة التحكم
    modal.Secret.from_name("hermes-dashboard"),
]

# نبني Hermes وواجهة Dashboard أثناء بناء الصورة
hermes_image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install(
        "git",
        "curl",
        "build-essential",
        "ca-certificates",
    )
    # تثبيت Node.js
    .run_commands(
        "curl -fsSL https://deb.nodesource.com/setup_22.x | bash -",
        "apt-get install -y nodejs",
        "node --version",
        "npm --version",
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
            "__pycache__",
        ],
    )
    .run_commands(
        f"pip install -e {HERMES_ROOT}",
        f"cd {HERMES_ROOT} && npm ci",
        f"cd {HERMES_ROOT} && npm run build --workspace web",
        f"test -f {HERMES_ROOT}/hermes_cli/web_dist/index.html",
    )
)

def build_runtime_environment() -> dict[str, str]:
    """Build the runtime environment and persist selected variables."""
    env = os.environ.copy()
    env["HERMES_HOME"] = HERMES_HOME
    env["HERMES_DASHBOARD_PUBLIC_URL"] = (
        "https://hoysama--hermes-api-server-dashboard.modal.run"
    )

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

            # يمنع كسر صيغة الملف إذا احتوت القيمة على سطر جديد.
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
    env = build_runtime_environment()

    env["API_SERVER_ENABLED"] = "true"
    env["API_SERVER_PORT"] = str(GATEWAY_PORT)
    env["API_SERVER_HOST"] = "0.0.0.0"
    env.setdefault("TELEGRAM_ALLOWED_USERS", "*")

    subprocess.run(
        ["hermes", "gateway", "run"],
        env=env,
        cwd=HERMES_ROOT,
        check=True,
    )


@app.function(
    image=hermes_image,
    volumes={HERMES_HOME: hermes_volume},
    secrets=hermes_secrets,
    min_containers=1,
    max_containers=1,
    timeout=86400,
)
@modal.web_server(
    port=DASHBOARD_PORT,
    startup_timeout=180,
)
def dashboard():
    """Run the authenticated Hermes web dashboard."""
    import os
    import subprocess
    env = build_runtime_environment()

    required_auth_variables = (
        "HERMES_DASHBOARD_BASIC_AUTH_USERNAME",
        "HERMES_DASHBOARD_BASIC_AUTH_PASSWORD",
        "HERMES_DASHBOARD_BASIC_AUTH_SECRET",
    )
    missing = [name for name in required_auth_variables if not env.get(name)]
    if missing:
        raise RuntimeError(
            "Dashboard authentication is not configured. "
            "Missing variable names: " + ", ".join(missing)
        )

    subprocess.run(
        [
            "hermes",
            "dashboard",
            "--host",
            "0.0.0.0",
            "--port",
            str(DASHBOARD_PORT),
            "--no-open",
            "--skip-build",
        ],
        env=env,
        cwd=HERMES_ROOT,
        check=True,
    )
