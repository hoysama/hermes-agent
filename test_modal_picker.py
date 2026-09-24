import modal
from modal_deploy import app, hermes_secrets, hermes_volume, HERMES_HOME

@app.function(
    secrets=hermes_secrets,
    volumes={HERMES_HOME: hermes_volume},
)
def test_picker():
    import sys
    sys.path.append("/workspace/hermes-agent")
    from hermes_cli.config import get_compatible_custom_providers, load_config
    from hermes_cli.model_switch import list_picker_providers
    import os
    
    cfg = load_config()
    custom_provs = get_compatible_custom_providers(cfg)
    
    print("API Keys from env:")
    print("IAMHC_API_KEY:", bool(os.environ.get("IAMHC_API_KEY")))
    print("FUTURE_API_KEY:", bool(os.environ.get("FUTURE_API_KEY")))
    
    providers = list_picker_providers(
        custom_providers=custom_provs,
        max_models=50,
    )
    
    print("Providers:")
    for p in providers:
        print("-", p.get("name"), "| slug:", p.get("slug"))

