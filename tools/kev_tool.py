#!/usr/bin/env python3
"""Kev System 1 Decision Tool for Hermes

Connects Hermes to the Modal Kev Service (hermes-kev) for sub-second
probabilistic classification and triage.  The model calls this tool when
it needs a fast, calibrated yes/no or category decision instead of
reasoning through the answer with Chain of Thought.
"""

import json
import logging
import requests
from tools.registry import registry, tool_error
from tools.tool_backend_helpers import get_modal_auth_headers

logger = logging.getLogger(__name__)

MODAL_KEV_URL = "https://hoysama--hermes-kev-decide.modal.run"


def kev_decide_tool(state: str, questions: dict) -> str:
    """Run Kev-0.8B System 1 inference: given a state (text to evaluate) and
    a dict of questions, return calibrated probabilities for each question."""
    state = (state or "").strip()
    if not state:
        return tool_error("state parameter is required — the text to evaluate.")
    if not questions or not isinstance(questions, dict):
        return tool_error("questions parameter must be a non-empty dict of question definitions.")

    # Validate question structure before sending
    for qid, qdef in questions.items():
        if not isinstance(qdef, dict):
            return tool_error(f"Question '{qid}' must be a dict with 'type' and 'instructions'.")
        qtype = qdef.get("type", "noul")
        if qtype not in ("noul", "choice", "score"):
            return tool_error(f"Question '{qid}' has unsupported type '{qtype}'. Use: noul, choice, score.")
        if qtype == "choice" and not qdef.get("options") and not qdef.get("criteria"):
            return tool_error(f"Question '{qid}' is type 'choice' but has no 'options' list.")

    try:
        response = requests.post(
            MODAL_KEV_URL,
            json={"state": state, "questions": questions},
            headers=get_modal_auth_headers(),
            timeout=30,
        )
        if response.status_code != 200:
            return tool_error(f"Kev service returned status {response.status_code}: {response.text}")

        data = response.json()
        if data.get("status") == "error":
            return tool_error(f"Kev error: {data.get('message')}")

        return json.dumps(
            {
                "success": True,
                "model": data.get("model", "kev-0.8b"),
                "answers": data.get("answers", {}),
                "latency_ms": data.get("latency_ms"),
            },
            ensure_ascii=False,
        )
    except Exception as exc:
        logger.error("Error calling Modal Kev endpoint: %s", exc)
        return tool_error(f"Failed to connect to Kev service: {exc}")


KEV_DECIDE_SCHEMA = {
    "name": "kev_decide",
    "description": (
        "Fast probabilistic classifier (System 1 / Kev-0.8B). "
        "Use this tool when you need a quick yes/no probability or category classification "
        "without full Chain of Thought reasoning. Accepts a 'state' (the text to evaluate) "
        "and 'questions' — a dict where each key is a question ID and the value defines the "
        "question type and options.\n\n"
        "Question types:\n"
        "- 'noul': yes/no probability (returns a float 0.0–1.0)\n"
        "- 'choice': pick one from a list of options (returns chosen option + confidence)\n"
        "- 'score': ordinal rating (returns score distribution)\n\n"
        "Best for: batch triage, content classification, safety checks, "
        "intent detection, priority sorting, and any decision where a calibrated "
        "probability is more useful than a generated explanation."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "state": {
                "type": "string",
                "description": "The text, message, or document to evaluate.",
            },
            "questions": {
                "type": "object",
                "description": (
                    "Dict of questions to answer about the state. Each key is a question ID, "
                    "each value is an object with: 'type' (noul|choice|score), "
                    "'instructions' (what to evaluate), and 'options' (list of choices, "
                    "required for 'choice' type). "
                    "Example: {\"is_urgent\": {\"type\": \"noul\", \"instructions\": \"Is this message urgent?\"}, "
                    "\"category\": {\"type\": \"choice\", \"options\": [\"bug\", \"feature\", \"question\"], "
                    "\"instructions\": \"Classify this message\"}}"
                ),
            },
        },
        "required": ["state", "questions"],
    },
}

registry.register(
    name="kev_decide",
    toolset="web",
    schema=KEV_DECIDE_SCHEMA,
    handler=lambda args, **kw: kev_decide_tool(
        state=args.get("state", ""),
        questions=args.get("questions", {}),
    ),
    emoji="⚡",
)
