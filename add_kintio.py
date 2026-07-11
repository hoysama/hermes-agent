import yaml
import sys
import os

config_path = "/home/hoy/.hermes/config.yaml"

if not os.path.exists(config_path):
    print("No config.yaml found.")
    sys.exit(1)

with open(config_path, "r") as f:
    config = yaml.safe_load(f)

custom_providers = config.get("custom_providers", [])

# Remove if exists
custom_providers = [p for p in custom_providers if p.get("name") != "kintio"]

# Add kintio
custom_providers.append({
    "name": "kintio",
    "base_url": "https://api.kintio.com",
    "api_mode": "anthropic_messages",
    "extra_headers": {
        "x-api-key": "sf_2aa126f77e6b318d4f64ee34a26ca6d927d83e9b518af8ca"
    }
})

config["custom_providers"] = custom_providers

with open(config_path, "w") as f:
    yaml.safe_dump(config, f, default_flow_style=False, sort_keys=False)

print("Updated config.yaml with kintio provider")
