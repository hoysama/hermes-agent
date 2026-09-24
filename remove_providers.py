import yaml
import sys

config_path = "/home/hoy/.hermes/config.yaml"
try:
    with open(config_path, "r") as f:
        cfg = yaml.safe_load(f)
except Exception as e:
    print(f"Failed to read config: {e}")
    sys.exit(1)

providers = cfg.get("custom_providers", [])
original_count = len(providers)
providers = [p for p in providers if p.get("name") not in ("kintio", "agentrouter")]
cfg["custom_providers"] = providers

try:
    with open(config_path, "w") as f:
        yaml.safe_dump(cfg, f, sort_keys=False, allow_unicode=True)
    print(f"Success! Removed {original_count - len(providers)} providers.")
except Exception as e:
    print(f"Failed to write config: {e}")
    sys.exit(1)
