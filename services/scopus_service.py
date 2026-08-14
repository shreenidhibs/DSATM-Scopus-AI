import os
import requests

from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("ELS_API_KEY")
INST_TOKEN = os.getenv("ELS_INST_TOKEN")

SCOPUS_SEARCH_URL = (
    "https://api.elsevier.com/content/search/scopus"
)

SCOPUS_AUTHOR_URL = (
    "https://api.elsevier.com/content/author/author_id"
)

def get_headers():

    if not API_KEY:
        raise RuntimeError(
            "ELS_API_KEY is missing from .env"
        )

    headers = {
        "X-ELS-APIKey": API_KEY,
        "Accept": "application/json"
    }

    if INST_TOKEN:
        headers["X-ELS-Insttoken"] = INST_TOKEN

    return headers

def get_author_profile(author_id: str):

    url = f"{SCOPUS_AUTHOR_URL}/{author_id}"

    params = {
        "view": "ENHANCED"
    }

    response = requests.get(
        url,
        headers=get_headers(),
        params=params,
        timeout=30
    )

    if not response.ok:
        raise RuntimeError(
            f"Scopus Author API error "
            f"{response.status_code}: "
            f"{response.text}"
        )

    return response.json()

def test_scopus_connection():

    params = {
        "query": "TITLE(test)",
        "count": 1
    }

    response = requests.get(
        SCOPUS_SEARCH_URL,
        headers=get_headers(),
        params=params,
        timeout=20
    )

    if response.ok:
        return {
            "success": True,
            "status_code": response.status_code,
            "message": "Scopus API connection successful"
        }

    return {
        "success": False,
        "status_code": response.status_code,
        "error": response.text
    }


def get_publications_by_author_id(
    author_id: str,
    count: int = 25,
    start: int = 0
):

    count = max(1, min(int(count or 25), 25))

    params = {
        "query": f"AU-ID({author_id})",
        "count": count,
        "start": start,
        "sort": "-coverDate",
        "view": "STANDARD"
    }

    response = requests.get(
        SCOPUS_SEARCH_URL,
        headers=get_headers(),
        params=params,
        timeout=30
    )

    if not response.ok:
        raise RuntimeError(
            f"Scopus API error "
            f"{response.status_code}: "
            f"{response.text}"
        )

    return response.json()

def get_all_publications_by_author_id(author_id: str, max_records: int = 500):
    all_publications = []
    start = 0
    page_size = 25
    total_results = 0
    while True:
        data = get_publications_by_author_id(author_id, count=page_size, start=start)
        results = data.get("search-results", {})
        try:
            total_results = int(results.get("opensearch:totalResults") or 0)
        except Exception:
            total_results = 0
        entries = list(results.get("entry") or [])
        if not entries:
            break
        all_publications.extend(entries)
        if len(all_publications) >= max_records:
            all_publications = all_publications[:max_records]
            break
        start += len(entries)
        if start >= total_results:
            break
    return {
        "author_id": str(author_id),
        "total_results": total_results,
        "retrieved": len(all_publications),
        "publications": all_publications,
        "truncated": total_results > len(all_publications),
    }
