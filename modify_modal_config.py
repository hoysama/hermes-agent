import yaml

with open("/tmp/modal_config.yaml", "r") as f:
    cfg = yaml.safe_load(f)

custom_providers = cfg.get("custom_providers", [])
cfg["custom_providers"] = [p for p in custom_providers if p.get("name") != "agentrouter"]

with open("/tmp/modal_config_updated.yaml", "w") as f:
    yaml.dump(cfg, f, default_flow_style=False, sort_keys=False)
