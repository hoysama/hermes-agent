import yaml
import sys

config_path = "/home/hoy/.hermes/config.yaml"

with open(config_path, "r") as f:
    config = yaml.safe_load(f)

custom_providers = config.get("custom_providers", [])

for p in custom_providers:
    if p.get("name") == "kintio":
        p["models"] = [
            "claude-opus-4.8",
            "claude-opus-4.7",
            "claude-opus-4.6",
            "claude-opus-4.5"
        ]

config["custom_providers"] = custom_providers

with open(config_path, "w") as f:
    yaml.safe_dump(config, f, default_flow_style=False, sort_keys=False)

print("Updated config.yaml with kintio models")
