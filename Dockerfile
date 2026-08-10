FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# poppler-utils is optional at runtime: when present the extractor uses pdftotext,
# which preserves reading order better than the pure-Python fallback.
RUN apt-get update \
    && apt-get install -y --no-install-recommends poppler-utils \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m pip install --no-cache-dir '.[api]'

EXPOSE 8000
CMD ["uvicorn", "resume_radar.api:app", "--host", "0.0.0.0", "--port", "8000"]
