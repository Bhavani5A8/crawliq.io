FROM python:3.11-slim

WORKDIR /app

# Install dependencies first (cached layer)
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy only the active backend modules — BUG-017: exclude dead Streamlit files
# (streamlit_app.py + streamlit_app_ui.py add 2400+ lines of unused attack surface)
COPY backend/main.py \
     backend/crawler.py \
     backend/gemini_analysis.py \
     backend/seo_optimizer.py \
     backend/technical_seo.py \
     backend/keyword_extractor.py \
     backend/keyword_pipeline.py \
     backend/keyword_scorer.py \
     backend/issues.py \
     backend/competitor.py \
     backend/competitor_analysis.py \
     backend/competitor_db.py \
     backend/groq_adapter.py \
     backend/openai_adapter.py \
     backend/claude_adapter.py \
     backend/ollama_adapter.py \
     backend/ai_analysis.py \
     backend/robust_fetch.py \
     backend/crawler_fetch_patch.py \
     backend/intent_classifier.py \
     backend/serp_engine.py \
     backend/serp_scraper.py \
     backend/link_graph.py \
     backend/content_dedup.py \
     backend/site_auditor.py \
     ./

# Copy the dashboard HTML (main.py serves backend/index.html from BASE_DIR)
# NOTE: backend/index.html is the full SPA dashboard; root index.html is the marketing page
COPY backend/index.html ./index.html

EXPOSE 7860

# BUG-019: /healthz endpoint is now available for container health probes
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:7860/healthz')"

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "7860"]
