#!/usr/bin/env python3
"""Audio Transcribe Tool for Hermes

Connects Hermes to the Modal Whisper Service (hermes-whisper)
to transcribe audio files/voice notes using Faster-Whisper on Modal GPU.
"""

import json
import logging
import requests
from tools.registry import registry, tool_error

logger = logging.getLogger(__name__)

MODAL_WHISPER_URL = "https://hoysama--hermes-whisper-transcribe.modal.run"


def audio_transcribe_tool(audio_url: str = "", audio_b64: str = "", language: str = None, task: str = "transcribe") -> str:
    """Transcribe audio files or voice notes into text using Faster-Whisper Large-v3 on Modal GPU."""
    audio_url = (audio_url or "").strip()
    audio_b64 = (audio_b64 or "").strip()

    if not audio_url and not audio_b64:
        return tool_error("audio_url or audio_b64 parameter is required.")

    try:
        payload = {
            "audio_url": audio_url,
            "audio_b64": audio_b64,
            "task": task or "transcribe",
        }
        if language:
            payload["language"] = language

        response = requests.post(
            MODAL_WHISPER_URL,
            json=payload,
            timeout=120,
        )
        if response.status_code != 200:
            return tool_error(f"Audio transcription failed with status {response.status_code}: {response.text}")

        data = response.json()
        if data.get("status") == "error":
            return tool_error(f"Whisper transcription error: {data.get('message')}")

        return json.dumps(
            {
                "success": True,
                "detected_language": data.get("detected_language"),
                "language_probability": data.get("language_probability"),
                "duration_seconds": data.get("duration_seconds"),
                "text": data.get("text"),
                "segments": data.get("segments", []),
            },
            ensure_ascii=False,
        )
    except Exception as exc:
        logger.error(f"Error calling Modal Whisper endpoint: {exc}")
        return tool_error(f"Failed to connect to audio transcription service: {exc}")


AUDIO_TRANSCRIBE_SCHEMA = {
    "name": "audio_transcribe",
    "description": (
        "Transcribe audio files, voice notes, lectures, or podcasts into highly accurate text with timestamps. "
        "Supports Arabic and over 90 languages using Faster-Whisper Large-v3 on GPU. "
        "Use this tool whenever a user provides a voice message, audio file URL, or audio recording."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "audio_url": {
                "type": "string",
                "description": "HTTP/HTTPS URL to the audio file or voice message (e.g. mp3, ogg, wav, m4a).",
            },
            "audio_b64": {
                "type": "string",
                "description": "Base64 encoded string of raw audio bytes (optional alternative to audio_url).",
            },
            "language": {
                "type": "string",
                "description": "Language code if known (e.g. 'ar' for Arabic, 'en' for English). Leave empty for auto-detection.",
            },
            "task": {
                "type": "string",
                "enum": ["transcribe", "translate"],
                "description": "'transcribe' for native transcription, 'translate' to translate audio into English.",
                "default": "transcribe",
            },
        },
        "required": [],
    },
}

registry.register(
    name="audio_transcribe",
    toolset="media",
    schema=AUDIO_TRANSCRIBE_SCHEMA,
    handler=lambda args, **kw: audio_transcribe_tool(
        audio_url=args.get("audio_url", ""),
        audio_b64=args.get("audio_b64", ""),
        language=args.get("language"),
        task=args.get("task", "transcribe"),
    ),
    emoji="🎙️",
)
