---
title: CrawlIQ
emoji: 🔍
colorFrom: indigo
colorTo: cyan
sdk: docker
pinned: false
app_port: 7860
---

# CrawlIQ — Free AI Technical SEO Audit Tool

[![Python](https://img.shields.io/badge/Python-3.11-blue?logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![HuggingFace](https://img.shields.io/badge/HuggingFace-Spaces-orange?logo=huggingface&logoColor=white)](https://huggingface.co/spaces/bhavani7/seo-project)
[![License: MIT](https://img.shields.io/badge/License-MIT-green)](LICENSE)

> **Built by [Teki Bhavani Shankar](https://www.linkedin.com/in/teki-bhavani-shankar-seo-professional/) — Technical SEO Specialist**
>
> [![LinkedIn](https://img.shields.io/badge/LinkedIn-Teki_Bhavani_Shankar-0A66C2?logo=linkedin&logoColor=white)](https://www.linkedin.com/in/teki-bhavani-shankar-seo-professional/)
> [![Live Demo](https://img.shields.io/badge/Live_App-crawliq.io-6366F1?logo=github&logoColor=white)](https://bhavani5a8.github.io/crawliq.io/)

---

## What is CrawlIQ?

Most SEO audit tools sit behind $100+/month paywalls. CrawlIQ changes that.

CrawlIQ is a **free, open-source Technical SEO platform** that crawls any website and delivers the same audit depth as Screaming Frog, SEMrush, and Surfer SEO — completely free. It detects 50+ on-page and technical SEO issues, scores every page 0–100, and generates AI-powered content fixes using Groq, Gemini, Claude, or OpenAI.

---

## Live Demo

> 🔗 **[https://bhavani5a8.github.io/crawliq.io/](https://bhavani5a8.github.io/crawliq.io/)**
>
> Backend: [https://huggingface.co/spaces/bhavani7/seo-project](https://huggingface.co/spaces/bhavani7/seo-project)

*Note: The HuggingFace backend sleeps after 48h of inactivity. First load may take 30–90 seconds.*

---

## Features

### Technical SEO Audit
- ✅ Per-page technical score (0–100) with letter grade (A–F)
- ✅ HTTP status code analysis (200, 3xx, 4xx, 5xx)
- ✅ Indexability assessment per page (noindex, canonical mismatch, robots.txt blocked)
- ✅ robots.txt and sitemap.xml validation
- ✅ Canonical URL detection and conflict flagging
- ✅ Redirect chain detection
- ✅ Core Web Vitals assessment integration
- ✅ Crawl budget analysis and internal link graph

### On-Page SEO
- ✅ Title tag audit (missing, too short <30, too long >60)
- ✅ Meta description audit (missing, duplicate, length)
- ✅ H1/H2/H3 heading structure analysis
- ✅ Open Graph and Twitter Card coverage check
- ✅ Image alt text audit
- ✅ Thin content detection (<300 words)
- ✅ Keyword extraction with TF-IDF scoring

### AI-Powered Fixes
- ✅ AI content analysis: Groq (Llama 3), Gemini, OpenAI, Claude
- ✅ Paste-ready optimized titles, meta descriptions, and H1s
- ✅ Live Optimization Table — edit and export directly

### Competitor & SERP Analysis
- ✅ Side-by-side competitor technical comparison
- ✅ Keyword gap analysis
- ✅ SERP position tracking and rank monitoring

### Reporting
- ✅ Excel export (.xlsx) — Full Report, Issues Report, Optimization Table, Technical Audit
- ✅ Per-page PDF export
- ✅ Schema.org structured data validator and generator

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.11, FastAPI, aiohttp |
| Crawling | aiohttp async BFS crawler, Playwright (optional) |
| AI Analysis | Groq (Llama 3), Gemini 1.5, OpenAI GPT-4o, Claude |
| Frontend | Vanilla HTML/CSS/JS (zero framework dependencies) |
| Deployment | HuggingFace Spaces (Docker), GitHub Pages |
| Database | SQLite (session storage) |

---

## Quick Start

### Local Development

```bash
# 1. Clone the repo
git clone https://github.com/Bhavani5A8/crawliq.io.git
cd crawliq.io

# 2. Create virtual environment
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r backend/requirements.txt

# 4. Set API keys (optional — AI features only)
export GROQ_API_KEY=gsk_...
export GEMINI_API_KEY=AIza...
# or create backend/.env with the above

# 5. Start the backend
cd backend
python main.py
```

The app will be available at `http://localhost:7860`.
API docs at `http://localhost:7860/docs`.

### Environment Variables

| Variable | Description | Required |
|---|---|---|
| `GROQ_API_KEY` | Groq API key (free tier available) | Optional |
| `GEMINI_API_KEY` | Google Gemini API key | Optional |
| `OPENAI_API_KEY` | OpenAI API key | Optional |
| `ANTHROPIC_API_KEY` | Anthropic Claude API key | Optional |
| `APP_BASE_URL` | Public URL for redirect callbacks | Optional |

---

## API Reference

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/crawl` | Start a crawl: `{"url": "https://example.com", "max_pages": 50}` |
| `GET` | `/crawl-status` | Poll crawl progress |
| `GET` | `/results` | Fetch all crawled page data |
| `POST` | `/analyze` | Run AI analysis on crawl results |
| `POST` | `/optimize` | Generate paste-ready SEO fixes |
| `GET` | `/export` | Download Excel report (.xlsx) |
| `POST` | `/technical-seo` | Run full technical SEO audit |
| `POST` | `/competitor` | Start competitor analysis |
| `GET` | `/healthz` | Health check |

---

## Project Structure

```
crawliq.io/
├── index.html              # GitHub Pages frontend (production)
├── backend/
│   ├── main.py             # FastAPI app + all API routes
│   ├── crawler.py          # Async BFS crawler (aiohttp)
│   ├── site_auditor.py     # Technical SEO audit engine
│   ├── seo_optimizer.py    # AI optimization pipeline
│   ├── competitor.py       # Competitor analysis
│   ├── serp_engine.py      # SERP tracking
│   ├── gemini_analysis.py  # Gemini AI adapter
│   ├── claude_adapter.py   # Claude AI adapter
│   ├── groq_adapter.py     # Groq AI adapter
│   ├── billing.py          # Stripe billing integration
│   ├── index.html          # HuggingFace frontend (served by FastAPI)
│   ├── requirements.txt
│   └── Dockerfile
├── robots.txt
├── sitemap.xml
└── README.md
```

---

## Built By

**Teki Bhavani Shankar** — Technical SEO Specialist

Built CrawlIQ because paywalls shouldn't block good SEO work. As an SEO intern with no budget for tools, I built the tool I wished existed. Now it's free for everyone.

[![LinkedIn](https://img.shields.io/badge/LinkedIn-Connect-0A66C2?logo=linkedin&logoColor=white)](https://www.linkedin.com/in/teki-bhavani-shankar-seo-professional/)
[![GitHub](https://img.shields.io/badge/GitHub-Bhavani5A8-181717?logo=github&logoColor=white)](https://github.com/Bhavani5A8)

---

## License

MIT — free to use, fork, and build on.
