import modal
app = modal.App("check-modal-code")
hermes_volume = modal.Volume.from_name("hermes-storage")
image = modal.Image.debian_slim(python_version="3.11")

@app.function(volumes={"/root/.hermes": hermes_volume}, image=image)
def check_code():
    import subprocess
    # Run bash in the container?
    # No, we can't easily check the image's code if we run a different image.
    pass
