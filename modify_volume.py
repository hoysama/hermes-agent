import modal
import yaml
import os

app = modal.App("modify-hermes-config")
hermes_volume = modal.Volume.from_name("hermes-storage")
image = modal.Image.debian_slim(python_version="3.11").pip_install("pyyaml")

@app.function(volumes={"/root/.hermes": hermes_volume}, image=image)
def update_config():
    config_path = "/root/.hermes/config.yaml"
    with open(config_path, "r") as f:
        config = yaml.safe_load(f)
    
    custom_providers = config.get("custom_providers", [])
    
    # Remove agentrouter
    custom_providers = [p for p in custom_providers if p.get("name") != "agentrouter"]

    for p in custom_providers:
        if p.get("name") == "kintio":
            p["api_key"] = "sf_2aa126f77e6b318d4f64ee34a26ca6d927d83e9b518af8ca"
            p["model"] = "claude-opus-4.8"
            p["models"] = ["claude-opus-4.5", "claude-opus-4.6", "claude-opus-4.7", "claude-opus-4.8"]
            p["discover_models"] = False
    
    config["custom_providers"] = custom_providers
    with open(config_path, "w") as f:
        yaml.safe_dump(config, f, default_flow_style=False, sort_keys=False)
    
    print("Updated kintio provider models and removed agentrouter")

@app.local_entrypoint()
def main():
    update_config.remote()
