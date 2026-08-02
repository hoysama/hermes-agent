# Workspace Rules for Hermes Modal Deployments & Provider Management

## Provider Addition/Removal Protocol
When adding or removing an inference provider for Hermes:
1. **Edit Config**: Always update the `custom_providers:` list directly inside `~/.hermes/config.yaml`. Do NOT hardcode provider logic into deployment scripts.
2. **Re-deploy All 3 Hermes Applications**: After editing `~/.hermes/config.yaml`, deploy all three Hermes instances to Modal in order:
   - **Hermes Personal**: `modal deploy modal_deploy.py`
   - **Hermes Support**: `modal deploy modal_deploy_support.py`
   - **Hermes Nabeh**: `modal deploy modal_deploy_nabeh.py`
