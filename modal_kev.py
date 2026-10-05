import modal
import time
from typing import Any, Dict

APP_NAME = "hermes-kev"

app = modal.App(APP_NAME)

kev_image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("git")
    .pip_install(
        "torch>=2.6",
        "transformers>=5.17",
        "peft>=0.21",
        "accelerate>=1.15",
        "scikit-learn",
        "pydantic>=2.9",
        "fastapi",
        "uvicorn",
        "git+https://github.com/jaredpalmer/kev.git",
    )
    .run_commands(
        "echo 'Preloading Kev-0.8B weights into container image layer...'",
        "python -c 'from kev.checkpoint import Checkpoint, LoadOptions; Checkpoint(\"jaredpalmer/kev-0.8b\").load(\"cpu\", LoadOptions())'",
    )
)

_MODEL = None
_TOK = None


def _get_engine():
    global _MODEL, _TOK
    if _MODEL is None or _TOK is None:
        from kev.checkpoint import Checkpoint, LoadOptions

        ck = Checkpoint("jaredpalmer/kev-0.8b")
        _TOK, _MODEL = ck.load("cpu", LoadOptions())
        _MODEL.eval()
    return _TOK, _MODEL


@app.function(
    image=kev_image,
    cpu=2.0,
    memory=6144,  # 6GB RAM ensures zero OOM risk during CPU inference
    timeout=60,
    min_containers=0,
    scaledown_window=30,
)
@modal.fastapi_endpoint(method="POST", requires_proxy_auth=True)
async def decide(data: dict) -> Dict[str, Any]:
    """
    Kev-0.8B System 1 Decision Endpoint.
    
    Accepts TypeSafe System One format:
    {
        "state": "Document, message, or code to evaluate",
        "questions": {
            "is_action_required": {"type": "noul", "instructions": "..."},
            "category": {"type": "choice", "options": ["a", "b", "c"], "instructions": "..."}
        }
    }
    """
    state = data.get("state")
    questions_raw = data.get("questions", {})

    if not state or not questions_raw:
        return {"error": "Missing 'state' or 'questions' parameter"}, 400

    import torch
    from kev.api import SystemOneRequest, Noul, Choice, Score, to_record, to_answers
    from kev.model import encode

    try:
        parsed_questions = {}
        for qid, qdata in questions_raw.items():
            qtype = qdata.get("type", "noul")
            instr = qdata.get("instructions")
            if qtype == "noul":
                parsed_questions[qid] = Noul(
                    type="noul",
                    instructions=instr,
                    criteria=qdata.get("criteria")
                )
            elif qtype == "choice":
                criteria = qdata.get("criteria")
                if not criteria and "options" in qdata:
                    criteria = {opt: None for opt in qdata["options"]}
                parsed_questions[qid] = Choice(
                    type="choice",
                    instructions=instr,
                    criteria=criteria or {}
                )
            elif qtype == "score":
                criteria = qdata.get("criteria") or qdata.get("options") or ["0", "1", "2"]
                parsed_questions[qid] = Score(
                    type="score",
                    instructions=instr,
                    criteria=criteria
                )
            else:
                return {"error": f"Unsupported question type: {qtype}"}, 400

        req = SystemOneRequest(
            state=state,
            model="kev-latest",
            questions=parsed_questions
        )
        rec, meta = to_record(req)

        tok, model = _get_engine()

        t0 = time.perf_counter()
        with torch.no_grad():
            enc = encode(tok, rec, option_isolation=model.option_isolation)
            probs = model.probs(enc)
            answers = to_answers(probs, meta)
        latency_ms = round((time.perf_counter() - t0) * 1000, 2)

        return {
            "status": "success",
            "model": "jaredpalmer/kev-0.8b",
            "answers": answers,
            "latency_ms": latency_ms,
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}, 500
