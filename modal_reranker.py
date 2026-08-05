import modal

APP_NAME = "hermes-reranker"

app = modal.App(APP_NAME)

rerank_image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install(
        "sentence-transformers",
        "torch",
        "fastapi",
        "uvicorn",
        "pydantic",
    )
)

@app.function(
    image=rerank_image,
    timeout=60,
    min_containers=0,
)
@modal.fastapi_endpoint(method="POST", requires_proxy_auth=True)
async def rerank(data: dict):
    """
    Rerank a list of search snippets or passages based on semantic relevance to a query.

    Payload:
    {
        "query": "What is Self-RAG?",
        "passages": ["passage 1 text", "passage 2 text", ...],
        "top_n": 5
    }
    """
    query = data.get("query")
    passages = data.get("passages", [])
    top_n = data.get("top_n", 5)

    if not query or not passages:
        return {"status": "error", "message": "Missing 'query' or 'passages' parameter"}, 400

    try:
        from sentence_transformers import CrossEncoder

        model = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2")
        
        pairs = [[query, p] for p in passages]
        scores = model.predict(pairs)

        indexed_scores = []
        for idx, score in enumerate(scores):
            indexed_scores.append((idx, float(score)))

        indexed_scores.sort(key=lambda x: x[1], reverse=True)

        ranked_items = []
        for idx, score in indexed_scores[:top_n]:
            ranked_items.append({
                "index": idx,
                "score": round(score, 4),
                "text": passages[idx],
            })

        return {
            "status": "success",
            "query": query,
            "total_input_passages": len(passages),
            "reranked_passages": ranked_items,
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}, 500
