"""Modal App: hermes-searxng

Runs SearXNG metasearch engine natively on Modal via @web_server.
Builds from source on debian_slim to avoid Alpine/micromamba incompatibilities.

Endpoint:
  GET  /search?q=<query>&format=json
"""

import modal
import os
import subprocess
import time
import socket

app = modal.App("hermes-searxng")

image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install(
        "git",
        "build-essential",
        "python3-dev",
        "libffi-dev",
        "libssl-dev",
        "libxml2-dev",
        "libxslt-dev",
        "zlib1g-dev",
    )
    .run_commands(
        "git clone https://github.com/searxng/searxng.git /usr/local/searxng",
        "cd /usr/local/searxng && pip install -r requirements.txt && pip install -e .",
        "mkdir -p /etc/searxng",
        """cat > /etc/searxng/settings.yml << 'EOF'
use_default_settings: true

general:
  instance_name: "hermes-searxng"
  debug: false

server:
  secret_key: "hermes-searxng-modal-2026-xK9mP2qL7vR3nW8"
  bind_address: "0.0.0.0"
  port: 8080
  limiter: false
  public_instance: false
  image_proxy: false

search:
  safe_search: 0
  autocomplete: ""
  default_lang: ""
  formats:
    - html
    - json

outgoing:
  request_timeout: 8.0
  max_request_timeout: 15.0
  useragent_suffix: ""
  pool_connections: 100
  pool_maxsize: 20

engines:
  - name: google
    disabled: false
    weight: 1.2
  - name: duckduckgo
    disabled: false
  - name: bing
    disabled: false
  - name: brave
    disabled: false
  - name: mojeek
    disabled: false
  - name: qwant
    disabled: false
  - name: startpage
    disabled: false
  - name: wikipedia
    disabled: false
  - name: wikidata
    disabled: true
  - name: yahoo
    disabled: true
  - name: yandex
    disabled: true
EOF""",
        """cat > /etc/searxng/limiter.toml << 'EOF'
[botdetection.ip_limit]
link_token = false
[botdetection.ip_lists]
pass_ip = ["127.0.0.1", "0.0.0.0"]
EOF""",
    )
)


@app.function(
    image=image,
    cpu=2.0,
    memory=1024,
    scaledown_window=120,
)
@modal.web_server(8080, startup_timeout=120.0, requires_proxy_auth=True)
def searxng_app():
    """Start SearXNG in the background and wait for it to be ready."""
    env = os.environ.copy()
    env["SEARXNG_SETTINGS_PATH"] = "/etc/searxng/settings.yml"
    env["SEARXNG_BIND_ADDRESS"] = "0.0.0.0"
    
    # Run SearXNG
    process = subprocess.Popen("cd /usr/local/searxng && python3 -m searx.webapp", env=env, shell=True)
    
    # Wait until port 8080 is open on IPv4 localhost
    start_time = time.time()
    ready = False
    
    while time.time() - start_time < 100:
        try:
            with socket.create_connection(("127.0.0.1", 8080), timeout=2.0):
                ready = True
                break
        except (ConnectionRefusedError, socket.timeout, OSError):
            time.sleep(1.0)
            
    if not ready:
        process.terminate()
        raise RuntimeError("SearXNG failed to bind to 127.0.0.1:8080 within 100 seconds.")
