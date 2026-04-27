FROM python:3.11-slim

WORKDIR /app

# Install dependencies first (cached layer)
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy all backend Python modules (wildcard — auto-picks up new files)
COPY backend/*.py ./

# Legacy dashboard (served at /dashboard, kept for backwards compat)
COPY backend/index.html ./index.html

# New landing page — served at / (same look as GitHub Pages root)
COPY index.html ./landing.html

# Tool pages — served at /pages/<name>
COPY backend/pages/ ./pages/

# Static assets (JS, CSS) — root /static/ is the single source of truth
# backend/static/ was removed in consolidation; all assets now live in /static/
COPY static/ ./static/

EXPOSE 7860

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:7860/healthz')"

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "7860"]
