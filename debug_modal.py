import modal

app = modal.App("hermes-debug")
hermes_volume = modal.Volume.from_name("hermes-storage")

hermes_image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("git")
    .add_local_dir(
        ".",
        remote_path="/workspace/hermes-agent",
        copy=True,
        ignore=[
            "**/.git",
            "**/node_modules",
            "**/.venv",
            "**/venv",
            "**/__pycache__",
            "**/tests",
        ],
    )
    .run_commands("pip install -e /workspace/hermes-agent")
)

@app.function(
    image=hermes_image,
    volumes={"/root/.hermes": hermes_volume},
)
def debug_providers():
    import sys
    sys.path.append("/workspace/hermes-agent")
    
    from hermes_cli.config import load_config, get_compatible_custom_providers
    import os
    os.environ["HERMES_HOME"] = "/root/.hermes"
    
    cfg = load_config()
    print("RAW CONFIG:", cfg.get("custom_providers"))
    try:
        custom_provs = get_compatible_custom_providers(cfg)
        print("COMPATIBLE PROVS:", custom_provs)
    except Exception as e:
        print("EXCEPTION:", e)
        custom_provs = cfg.get("custom_providers")
        
    from hermes_cli.model_switch import list_picker_providers
    providers = list_picker_providers(
        user_providers=cfg.get("providers"),
        custom_providers=custom_provs,
        max_models=50,
        include_moa=True,
    )
    print("PICKER PROVIDERS:")
    for p in providers:
        print(p.get("slug"), p.get("name"))

if __name__ == "__main__":
    with app.run():
        debug_providers.remote()
