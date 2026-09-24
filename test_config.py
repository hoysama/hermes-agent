import modal
import yaml
import sys
import os

app = modal.App("test-hermes-config")
hermes_volume = modal.Volume.from_name("hermes-storage")
# Mount the local codebase
local_dir = modal.Mount.from_local_dir("/home/hoy/projects/hermes-agent", remote_path="/app")

image = modal.Image.debian_slim(python_version="3.11").pip_install("pyyaml")

@app.function(volumes={"/root/.hermes": hermes_volume}, mounts=[local_dir], image=image)
def test_providers():
    sys.path.insert(0, "/app")
    
    # Force HERMES_HOME to be /root/.hermes
    os.environ["HERMES_HOME"] = "/root/.hermes"
    
    import hermes_cli.config
    from hermes_cli.model_switch import list_picker_providers
    
    cfg = hermes_cli.config.load_config()
    custom_provs = hermes_cli.config.get_compatible_custom_providers(cfg)
    
    providers = list_picker_providers(
        custom_providers=custom_provs,
        max_models=50,
        include_moa=True,
    )
    
    print(f"Total providers found: {len(providers)}")
    for p in providers:
        print(f"Provider: {p.get('name')} (slug: {p.get('slug')}), total_models: {p.get('total_models')}, is_user_defined: {p.get('is_user_defined')}")

@app.local_entrypoint()
def main():
    test_providers.remote()
