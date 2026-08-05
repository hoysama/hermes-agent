import modal

app = modal.App("hermes-whisper")

image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("ffmpeg")
    .pip_install("faster-whisper", "nvidia-cublas-cu12", "nvidia-cudnn-cu12", "requests", "fastapi[standard]")
    .env({
        "LD_LIBRARY_PATH": "/usr/local/lib/python3.11/site-packages/nvidia/cublas/lib:/usr/local/lib/python3.11/site-packages/nvidia/cudnn/lib"
    })
)


@app.function(
    image=image,
    gpu="any",
    timeout=300,
    scaledown_window=60,
)
@modal.fastapi_endpoint(method="POST")
def transcribe(data: dict):
    """Transcribe audio from a URL or base64 data using Faster-Whisper on Modal GPU with optional translation."""
    import base64
    import tempfile
    import requests
    import glob
    import ctypes

    # Preload nvidia CUDA shared libraries (cublas, cudnn) for ctranslate2
    for pattern in ["/usr/local/lib/python*/*packages/nvidia/*/lib/*.so*"]:
        for path in sorted(glob.glob(pattern)):
            try:
                ctypes.CDLL(path)
            except Exception:
                pass

    from faster_whisper import WhisperModel

    audio_url = data.get("audio_url", "").strip()
    audio_b64 = data.get("audio_b64", "").strip()
    language = data.get("language", None)
    task = data.get("task", "transcribe")  # "transcribe" or "translate"

    if not audio_url and not audio_b64:
        return {"status": "error", "message": "audio_url or audio_b64 parameter is required"}

    try:
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as temp_file:
            if audio_url:
                resp = requests.get(audio_url, timeout=60)
                if resp.status_code != 200:
                    return {"status": "error", "message": f"Failed to download audio: status {resp.status_code}"}
                temp_file.write(resp.content)
            else:
                raw_bytes = base64.b64decode(audio_b64)
                temp_file.write(raw_bytes)
            temp_path = temp_file.name

        # Load Faster-Whisper Model (large-v3 float16 on GPU)
        model = WhisperModel("large-v3", device="cuda", compute_type="float16")
        segments, info = model.transcribe(
            temp_path,
            language=language,
            task=task,
            beam_size=5,
            vad_filter=True,  # Voice activity detection filter for clean segments
        )

        segment_list = []
        full_text = []
        for segment in segments:
            full_text.append(segment.text)
            segment_list.append({
                "start": round(segment.start, 2),
                "end": round(segment.end, 2),
                "text": segment.text.strip(),
            })

        return {
            "status": "success",
            "detected_language": info.language,
            "language_probability": round(info.language_probability, 4),
            "duration_seconds": round(info.duration, 2),
            "text": " ".join(full_text).strip(),
            "segments": segment_list,
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}
