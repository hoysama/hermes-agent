import modal
app = modal.App("read-logs")
hermes_volume = modal.Volume.from_name("hermes-storage")
image = modal.Image.debian_slim(python_version="3.11")

@app.function(volumes={"/root/.hermes": hermes_volume}, image=image)
def read_logs():
    import os
    with open("/root/.hermes/logs/gateway.log", "r") as f:
        print("".join(f.readlines()[-150:]))

