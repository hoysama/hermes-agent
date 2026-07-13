import yaml

config_path = "/home/hoy/.hermes/config.yaml"

with open(config_path, "r") as f:
    config = yaml.safe_load(f)

if "memory" not in config:
    config["memory"] = {}

# Re-enable built-in memory (MEMORY.md + USER.md)
config["memory"]["memory_enabled"] = True
config["memory"]["user_profile_enabled"] = True

# Keep Holographic as additional deep recall layer
config["memory"]["provider"] = "holographic"

# Ensure auto_extract is boolean false (not string "false")
if "plugins" not in config:
    config["plugins"] = {}
if "hermes-memory-store" not in config.get("plugins", {}).get("entries", {}):
    if "entries" not in config["plugins"]:
        config["plugins"]["entries"] = {}
    config["plugins"]["entries"]["hermes-memory-store"] = {}
config["plugins"]["entries"]["hermes-memory-store"]["auto_extract"] = False

with open(config_path, "w") as f:
    yaml.safe_dump(config, f, default_flow_style=False, sort_keys=False)

print("Done: Hybrid mode enabled (Built-in ON + Holographic ON + auto_extract OFF)")
