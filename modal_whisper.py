import modal

app = modal.App("hermes-whisper")

DIALECT_MODEL = "/root/models/whisper-arabic-dialectal-ct2"
TURBO_BASE_MODEL = "deepdml/faster-whisper-large-v3-turbo-ct2"
DEFAULT_MODEL = DIALECT_MODEL

image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("ffmpeg")
    .pip_install(
        "faster-whisper",
        "ctranslate2",
        "transformers",
        "torch",
        "nvidia-cublas-cu12",
        "nvidia-cudnn-cu12",
        "requests",
        "fastapi[standard]",
        "huggingface_hub",
    )
    .env({
        "LD_LIBRARY_PATH": "/usr/local/lib/python3.11/site-packages/nvidia/cublas/lib:/usr/local/lib/python3.11/site-packages/nvidia/cudnn/lib"
    })
    .run_commands(
        "echo 'Pre-converting oddadmix/whisper-large-v3-turbo-arabic-dialectal to CT2 (2026-09-07)'",
        "mkdir -p /root/models",
        "ct2-transformers-converter --model oddadmix/whisper-large-v3-turbo-arabic-dialectal --output_dir /root/models/whisper-arabic-dialectal-ct2 --copy_files tokenizer.json --quantization float16 --force",
        "python -c 'from huggingface_hub import hf_hub_download; import shutil; p = hf_hub_download(\"openai/whisper-large-v3-turbo\", \"preprocessor_config.json\"); shutil.copy(p, \"/root/models/whisper-arabic-dialectal-ct2/preprocessor_config.json\")'",
        "python -c 'from faster_whisper import download_model; download_model(\"deepdml/faster-whisper-large-v3-turbo-ct2\")'"
    )
)

_MODEL = None


def _load_cuda_libs():
    import glob
    import ctypes

    for pattern in ["/usr/local/lib/python*/*packages/nvidia/*/lib/*.so*"]:
        for path in sorted(glob.glob(pattern)):
            try:
                ctypes.CDLL(path)
            except Exception:
                pass


def _get_model(model_name: str = DEFAULT_MODEL):
    global _MODEL
    if _MODEL is None:
        _load_cuda_libs()
        from faster_whisper import WhisperModel
        import os

        target = model_name
        if not os.path.exists(target) and target == DIALECT_MODEL:
            target = TURBO_BASE_MODEL
        _MODEL = WhisperModel(target, device="cuda", compute_type="float16")
    return _MODEL


@app.function(
    image=image,
    gpu="any",
    timeout=300,
    scaledown_window=120,
)
@modal.fastapi_endpoint(method="POST", requires_proxy_auth=True)
def transcribe(data: dict):
    """Transcribe audio from a URL or base64 data using Faster-Whisper Large-v3-Turbo on Modal GPU."""
    import base64
    import tempfile
    import requests

    audio_url = data.get("audio_url", "").strip()
    audio_b64 = data.get("audio_b64", "").strip()
    language = data.get("language", None)
    task = data.get("task", "transcribe")  # "transcribe" or "translate"
    req_model = data.get("model", "")
    model_name = DEFAULT_MODEL if not req_model or req_model in ("turbo", "large-v3-turbo", "large-v3", "dialect", "arabic", "default") else req_model
    word_timestamps = data.get("word_timestamps", False)

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

        initial_prompt = data.get("initial_prompt", None)

        model = _get_model(model_name)
        segments, info = model.transcribe(
            temp_path,
            language=language,
            task=task,
            beam_size=5,
            initial_prompt=initial_prompt,
            condition_on_previous_text=False,
            repetition_penalty=1.1,
            vad_filter=True,  # Voice activity detection filter for clean segments
            word_timestamps=word_timestamps,
        )

        segment_list = []
        full_text = []
        for segment in segments:
            full_text.append(segment.text)
            seg_dict = {
                "start": round(segment.start, 2),
                "end": round(segment.end, 2),
                "text": segment.text.strip(),
            }
            if word_timestamps and hasattr(segment, "words") and segment.words:
                seg_dict["words"] = [
                    {"word": w.word, "start": round(w.start, 2), "end": round(w.end, 2), "probability": round(w.probability, 2)}
                    for w in segment.words
                ]
            segment_list.append(seg_dict)

        final_text = " ".join(full_text).strip()
        print(f"🎙️ [Whisper Transcribe] Language: {info.language} ({info.language_probability:.2f}), Duration: {info.duration:.2f}s | Result: '{final_text}'")

        return {
            "status": "success",
            "detected_language": info.language,
            "language_probability": round(info.language_probability, 4),
            "duration_seconds": round(info.duration, 2),
            "text": final_text,
            "segments": segment_list,
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}
