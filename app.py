"""FastAPI server. Run with:  python app.py   (then open http://localhost:8000)

Endpoints
  GET  /api/status                 -> which LLM is configured
  GET  /api/cases                  -> case files (document collections) + sample claims
  GET  /api/cases/{key}/exhibits   -> every chunk in a case (for the evidence browser)
  GET  /api/trial?case=..&claim=.. -> Server-Sent Events stream of the trial
  POST /api/upload                 -> upload your own docs as a new case file

Deployed on Render (see render.yaml + README).
"""

from __future__ import annotations

import json
import os
import re
import time
from collections import defaultdict, deque
from pathlib import Path

import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

from courtroom import config
from courtroom.ingest import SUPPORTED, list_collections
from courtroom.llm import LLM
from courtroom.trial import get_retriever, reset_cache, run_trial

app = FastAPI(title="Courtroom RAG")
STATIC = Path(__file__).parent / "static"

# only matters if the frontend is ever hosted on a different domain than this server
origins = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "*").split(",") if o.strip()]
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST"], allow_headers=["*"])

# once it's public anyone can hit it and burn my groq quota, so: simple per-IP limit
TRIALS_PER_HOUR = int(os.getenv("TRIALS_PER_HOUR", "30"))
UPLOADS_ALLOWED = os.getenv("ALLOW_UPLOADS", "true").lower() == "true"
_hits: dict[str, deque] = defaultdict(deque)


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    return fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "?")


def _rate_limit(request: Request):
    now, q = time.time(), _hits[_client_ip(request)]
    while q and now - q[0] > 3600:
        q.popleft()
    if len(q) >= TRIALS_PER_HOUR:
        raise HTTPException(429, f"Limit is {TRIALS_PER_HOUR} trials per hour. Try again later.")
    q.append(now)


def _check_case(key: str) -> str:
    keys = {c["key"] for c in list_collections()}
    if key not in keys:
        raise HTTPException(404, f"Unknown case file '{key}'")
    return key


@app.get("/api/status")
def status():
    return {**LLM().describe(), "uploads": UPLOADS_ALLOWED}


@app.get("/api/cases")
def cases():
    return list_collections()


@app.get("/api/cases/{key:path}/exhibits")
def exhibits(key: str):
    _check_case(key)
    return [c.to_dict() for c in get_retriever(key).chunks]


@app.get("/api/trial")
def trial(request: Request, case: str, claim: str, rounds: int = 2):
    _check_case(case)
    claim = claim.strip()[:400]
    if len(claim) < 5:
        raise HTTPException(400, "Write a claim of at least a few words.")
    try:
        _rate_limit(request)
        limited = None
    except HTTPException as e:
        limited = e.detail  # send it as an event, EventSource can't read error bodies

    def stream():
        if limited:
            yield f"data: {json.dumps({'type': 'error', 'message': limited})}\n\n"
            return
        try:
            for ev in run_trial(claim, case, rounds=max(1, min(rounds, 2))):
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
        except Exception as e:  # don't leave the browser hanging forever
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/upload")
async def upload(name: str = Form(""), files: list[UploadFile] = File(...)):
    if not UPLOADS_ALLOWED:
        raise HTTPException(403, "Uploads are turned off on this server.")
    slug = re.sub(r"[^a-z0-9]+", "_", (name or "my_documents").lower()).strip("_") or "my_documents"
    slug = f"{slug[:40]}_{int(time.time()) % 100000}"
    folder = config.UPLOAD_DIR / slug
    folder.mkdir(parents=True, exist_ok=True)
    saved = []
    for f in files[:10]:
        fname = Path(f.filename or "file.txt").name
        if Path(fname).suffix.lower() not in SUPPORTED:
            continue
        data = await f.read()
        if len(data) > 15 * 1024 * 1024:
            continue  # 15MB is plenty for a demo
        (folder / fname).write_bytes(data)
        saved.append(fname)
    if not saved:
        folder.rmdir()
        raise HTTPException(400, "No supported files. Use .pdf, .md or .txt.")
    reset_cache()
    return {"key": f"uploads/{slug}", "saved": saved}


# the UI. mounted last so /api/* wins. html=True serves index.html at "/"
app.mount("/", StaticFiles(directory=STATIC, html=True), name="site")


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    host = "0.0.0.0" if os.getenv("PORT") else "127.0.0.1"
    uvicorn.run("app:app", host=host, port=port, reload=False)
