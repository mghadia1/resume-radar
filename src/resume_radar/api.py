"""HTTP wrapper around :func:`resume_radar.analyze`.

Exists so the workflow engine (n8n) can call the scorer over the network instead of
re-implementing the checks inside a Code node. The endpoint returns exactly what the
CLI returns, so there is only one scoring implementation to keep honest.

Run it with::

    uvicorn resume_radar.api:app --port 8000
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse

from . import __version__, analyze, extract

app = FastAPI(title="ResumeRadar", version=__version__)

INDEX = Path(__file__).with_name("index.html")

# Bound the in-memory copy from the parsed upload before writing a temp file.
# Multipart parsing and spooling happen before this handler runs.
MAX_UPLOAD_BYTES = 10 * 1024 * 1024


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    """Serve the upload page, so the service is usable without curl."""
    return INDEX.read_text(encoding="utf-8")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "version": __version__}


@app.post("/score")
async def score(
    file: UploadFile = File(...),
    job_description: str = Form(""),
    with_suggestions: bool = Form(False),
) -> dict:
    """Score one uploaded resume and return the same dict the CLI prints."""
    suffix = Path(file.filename or "resume.pdf").suffix.lower()
    if suffix not in {".pdf", ".docx", ".txt", ".md"}:
        raise HTTPException(status_code=415, detail=f"unsupported file type: {suffix}")

    # One extra byte distinguishes an exact-limit upload from an oversized one.
    payload = await file.read(MAX_UPLOAD_BYTES + 1)
    if not payload:
        raise HTTPException(status_code=400, detail="empty upload")
    if len(payload) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="file larger than 10 MB")

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / f"upload{suffix}"
        path.write_bytes(payload)
        try:
            analysis = analyze(path, job_description, with_suggestions=with_suggestions)
        except extract.ExtractionError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    result = analysis.to_dict()
    # Report the name the caller uploaded, not the temp path it was written to.
    result["resume"] = file.filename or "upload"
    return result
