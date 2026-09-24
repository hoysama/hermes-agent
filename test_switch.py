from hermes_cli.config import load_config
import hermes_cli.models

cfg = load_config()

# Patch models.py to fix slug matching using name
with open("/home/hoy/projects/hermes-agent/hermes_cli/models.py", "r") as f:
    content = f.read()

content = content.replace(
    'provider_config = next((p for p in custom_provs if p.get("slug") == slug_to_match), None)',
    'provider_config = next((p for p in custom_provs if p.get("name", "").lower() == slug_to_match), None)'
)

with open("/home/hoy/projects/hermes-agent/hermes_cli/models.py", "w") as f:
    f.write(content)

import importlib
importlib.reload(hermes_cli.models)

res = hermes_cli.models.validate_requested_model(
    "claude-opus-4.8",
    "custom:kintio",
    api_key="sf_2aa126f77e6b318d4f64ee34a26ca6d927d83e9b518af8ca",
    base_url="https://api.kintio.com/v1",
    api_mode="anthropic_messages"
)
print("Result:")
print(res)
