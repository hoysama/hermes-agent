import sys
sys.path.append("/home/hoy/projects/hermes-agent")

import os
os.environ["IAMHC_API_KEY"] = "fake"
os.environ["FUTURE_API_KEY"] = "fake"

import yaml
from hermes_cli.config import get_compatible_custom_providers
from hermes_cli.model_switch import list_picker_providers
from hermes_cli.providers import get_label

with open("/tmp/config.yaml", "r") as f:
    cfg = yaml.safe_load(f)

custom_provs = get_compatible_custom_providers(cfg)

providers = list_picker_providers(
    current_provider="openrouter",
    current_base_url="",
    user_providers=cfg.get("providers"),
    custom_providers=custom_provs,
    max_models=50,
    include_moa=True,
)

for p in providers:
    print(p.get("name"), p.get("slug"))
