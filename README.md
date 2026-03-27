# SEO Crawler Dashboard

A minimal, functional SEO crawler with a FastAPI backend and plain HTML/JS frontend.

## Project Structure

```
seo_crawler/
├── main.py          # FastAPI app + API routes
├── crawler.py       # Core crawling logic (SEOCrawler class)
├── index.html       # Frontend dashboard (open in browser)
└── requirements.txt
```

## Setup & Run

### 1. Create a virtual environment (recommended)

```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Start the backend

```bash
python main.py
# or:
uvicorn main:app --reload --port 8000
```

The API will be available at `http://localhost:8000`.
Docs at `http://localhost:8000/docs`.

### 4. Open the dashboard

Visit **http://localhost:8000** in your browser — FastAPI now serves the UI directly.

> No need to open `index.html` as a file anymore. The frontend and API share the same
> origin so the Excel download works without any CORS issues.

## Usage

1. Enter a website URL (e.g. `https://example.com`)
2. Set max pages (default 50, max 100)
3. Click **Start Crawl** — the backend crawls synchronously and returns when done
4. Results appear in the table; click column headers to sort
5. Click **Export Excel** → modal appears → **Download .xlsx**

## API Endpoints

| Method | Path       | Description                        |
|--------|------------|------------------------------------|
| POST   | /crawl     | `{ "url": "...", "max_pages": 50 }` — starts crawl |
| GET    | /results   | Returns status + all crawled data  |
| GET    | /export    | Downloads the Excel report (.xlsx) |

## Data Extracted Per Page

- URL
- Status Code
- Page Title
- Meta Description
- H1 Tag
- Canonical URL
- Internal Links Count
