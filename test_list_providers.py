import yaml
from hermes_cli.config import get_compatible_custom_providers
from hermes_cli.model_switch import list_picker_providers
import pprint

with open('/tmp/config_verify.yaml', 'r') as f:
    cfg = yaml.safe_load(f)

custom_provs = get_compatible_custom_providers(cfg)
providers = list_picker_providers(custom_providers=custom_provs, current_provider="custom:kintio", current_model="claude-opus-4")
kintio = [p for p in providers if p["slug"] == "custom:kintio"]
print("\nList picker output:")
pprint.pprint(kintio)
