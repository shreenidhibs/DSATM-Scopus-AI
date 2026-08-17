from __future__ import annotations

import io
import base64
import os
import re
import threading
from datetime import datetime
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any
from urllib.parse import quote


import pandas as pd
import requests
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from services.scopus_service import (
    test_scopus_connection,
    get_publications_by_author_id,
    get_all_publications_by_author_id,
)
faculty_meta_cache = []



BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
app = FastAPI(title="DSI Faculty Scopus Research Analytics", version="2.0.0")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")

STATE: dict[str, Any] = {
    "df": None,
    "filename": None,
    "institution_keyword": "Dayananda Sagar Academy of Technology and Management",
    "mapping": {},
    "faculty_meta": [],
    "departments": [],
    "overall": {},
}


LIVE_CACHE: dict[str, dict[str, Any]] = {}
SCOPUS_SEARCH_URL = "https://api.elsevier.com/content/search/scopus"

DSATM_INSTITUTION_NAME = (
    "Dayananda Sagar Academy of Technology and Management"
)

DSATM_SCOPUS_AFFILIATION_ID = "60283483"

DSATM_AUTHOR_DIRECTORY_FILE = BASE_DIR / "DSATM_Author_Directory.xlsx"

AUTHOR_DIRECTORY_PROGRESS: dict[str, Any] = {
    "running": False,
    "stage": "idle",
    "percent": 0,
    "current": 0,
    "total": 0,
    "authors_found": 0,
    "message": "Author directory has not been built yet.",
    "error": "",
    "completed": False,
}
AUTHOR_DIRECTORY_PROGRESS_LOCK = threading.Lock()



DSATM_AUTHOR_DIRECTORY: dict[str, dict[str, Any]] = {}
DSATM_AUTHOR_PREFIXES_LOADED: set[str] = set()


def scopus_headers() -> dict[str, str]:
    api_key = os.getenv("ELS_API_KEY", "").strip()
    if not api_key:
        raise HTTPException(500, "ELS_API_KEY is missing. Add it to the .env file in the project root.")
    headers = {"X-ELS-APIKey": api_key, "Accept": "application/json"}
    inst_token = os.getenv("ELS_INST_TOKEN", "").strip()
    if inst_token:
        headers["X-ELS-Insttoken"] = inst_token
    return headers


def _scopus_request(
    author_id: str,
    start: int = 0,
    count: int = 25
) -> dict[str, Any]:
    author_id = re.sub(r"\D", "", str(author_id or ""))
    if not author_id:
        raise HTTPException(400, "Enter a valid numeric Scopus Author ID.")

    count = max(1, min(int(count or 25), 25))

    response = requests.get(
        SCOPUS_SEARCH_URL,
        headers=scopus_headers(),
        params={
            "query": f"AU-ID({author_id})",
            "count": count,
            "start": start,
            "sort": "-coverDate",
            "view": "STANDARD",
        },
        timeout=30,
    )

    if not response.ok:
        detail = response.text[:800]
        raise HTTPException(
            response.status_code,
            f"Scopus API error: {detail}"
        )

    return response.json()


def _scopus_institution_request(
    start: int = 0,
    count: int = 25,
    query_extra: str = ""
) -> dict[str, Any]:
    """
    Search DSATM publications using only the normal Scopus Search API.
    No facets and no Author Search API are used.
    """

    count = max(1, min(int(count or 25), 25))

    query = f"AF-ID({DSATM_SCOPUS_AFFILIATION_ID})"

    if query_extra:
        query = f"{query} AND {query_extra}"

    response = requests.get(
        SCOPUS_SEARCH_URL,
        headers=scopus_headers(),
        params={
            "query": query,
            "start": start,
            "count": count,
            "sort": "-coverDate",
            "view": "STANDARD",
        },
        timeout=30,
    )

    if not response.ok:
        raise HTTPException(
            status_code=response.status_code,
            detail=(
                "DSATM Scopus institution search failed: "
                + response.text[:800]
            ),
        )

    return response.json()


def _entry_author_affiliation_url(entry: dict[str, Any]) -> str:
    """
    Return the Scopus author-affiliation URL for a publication,
    when Scopus exposes one in the search result links.
    """

    links = entry.get("link") or []

    if isinstance(links, dict):
        links = [links]

    for link in links:
        if not isinstance(link, dict):
            continue

        ref = str(link.get("@ref") or "").strip().lower()
        href = str(link.get("@href") or "").strip()

        if ref == "author-affiliation" and href:
            return href

    # Fallback from EID / Scopus document identifier.
    scopus_id = str(entry.get("dc:identifier") or "").strip()

    if scopus_id.upper().startswith("SCOPUS_ID:"):
        sid = scopus_id.split(":", 1)[1].strip()
        if sid:
            return (
                "https://api.elsevier.com/content/abstract/scopus_id/"
                f"{sid}?field=author,affiliation"
            )

    return ""


def _extract_authors_from_abstract_payload(
    payload: dict[str, Any]
) -> list[dict[str, Any]]:
    """
    Parse authors + Scopus Author IDs from an Abstract/Author-Affiliation response.
    """

    root = payload.get("abstracts-retrieval-response") or payload

    authors_block = root.get("authors") or {}

    if isinstance(authors_block, dict):
        raw_authors = authors_block.get("author") or []
    else:
        raw_authors = []

    if isinstance(raw_authors, dict):
        raw_authors = [raw_authors]

    authors: list[dict[str, Any]] = []

    for item in raw_authors:
        if not isinstance(item, dict):
            continue

        author_id = str(
            item.get("@auid")
            or item.get("authid")
            or item.get("auid")
            or ""
        ).strip()

        indexed = str(
            item.get("ce:indexed-name")
            or item.get("indexed-name")
            or ""
        ).strip()

        given = str(
            item.get("ce:given-name")
            or item.get("given-name")
            or ""
        ).strip()

        surname = str(
            item.get("ce:surname")
            or item.get("surname")
            or ""
        ).strip()

        name = indexed or " ".join(
            part for part in (given, surname) if part
        ).strip()

        # Collect affiliations attached directly to the author.
        afids: list[str] = []
        affiliation = item.get("affiliation") or []

        if isinstance(affiliation, dict):
            affiliation = [affiliation]

        for aff in affiliation:
            if not isinstance(aff, dict):
                continue

            afid = str(
                aff.get("@id")
                or aff.get("afid")
                or aff.get("id")
                or ""
            ).strip()

            if afid:
                afids.append(afid)

        if author_id and name:
            authors.append({
                "name": name,
                "author_id": re.sub(r"\D", "", author_id),
                "afids": afids,
            })

    return authors


def _get_publication_authors(entry: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Obtain authors for one Scopus publication.

    First use any author list already present in the normal Scopus Search
    response. If Author IDs are missing, follow the publication's
    author-affiliation link and request only author + affiliation fields.
    """

    result: list[dict[str, Any]] = []

    # 1. Try normal search-result author objects.
    raw_authors = entry.get("author") or []

    if isinstance(raw_authors, dict):
        raw_authors = [raw_authors]

    for item in raw_authors:
        if not isinstance(item, dict):
            continue

        name = str(
            item.get("authname")
            or item.get("ce:indexed-name")
            or item.get("preferred-name")
            or ""
        ).strip()

        author_id = str(
            item.get("authid")
            or item.get("@auid")
            or item.get("auid")
            or ""
        ).strip()

        if name and author_id:
            result.append({
                "name": name,
                "author_id": re.sub(r"\D", "", author_id),
                "afids": [],
            })

    if result:
        return result

    # 2. Follow the author-affiliation link for this paper.
    url = _entry_author_affiliation_url(entry)

    if not url:
        return []

    response = requests.get(
        url,
        headers=scopus_headers(),
        timeout=30,
    )

    if not response.ok:
        return []

    try:
        payload = response.json()
    except Exception:
        return []

    return _extract_authors_from_abstract_payload(payload)


def _name_match_score(query: str, candidate: str) -> int:
    """
    Match variants such as:
      Shreenidhi B S
      Shreenidhi, B.S.
      B S Shreenidhi
      Shreenidhi
    """

    q = norm(query)
    c = norm(candidate)

    if not q or not c:
        return 0

    q_compact = re.sub(r"[^a-z0-9]", "", q)
    c_compact = re.sub(r"[^a-z0-9]", "", c)

    if q_compact == c_compact:
        return 100

    q_tokens = q.split()
    c_tokens = c.split()

    q_set = set(q_tokens)
    c_set = set(c_tokens)

    score = 0

    if q == c:
        score = 100
    elif q in c or c in q:
        score = 92

    overlap = len(q_set & c_set)

    if overlap:
        token_score = int(
            100 * overlap / max(len(q_set), len(c_set))
        )
        score = max(score, token_score)

        if q_set.issubset(c_set) or c_set.issubset(q_set):
            score = max(score, 88)

    ratio = int(
        100
        * SequenceMatcher(
            None,
            q_compact,
            c_compact
        ).ratio()
    )

    score = max(score, ratio)

    return min(score, 100)


def _candidate_query_terms(faculty_name: str) -> list[str]:
    """
    Build several normal Scopus Search queries from the typed name.

    We search both surname and first-name indexes because Scopus may store
    a person's indexed name in a different order.
    """

    tokens = [
        token
        for token in norm(faculty_name).split()
        if token
    ]

    useful = [
        token
        for token in tokens
        if len(token) >= 2
    ]

    queries: list[str] = []

    for token in useful:
        queries.append(f"AUTHLASTNAME({token})")
        queries.append(f"AUTHFIRST({token})")

    # Also try the longest token first; for many Indian names this is the
    # most distinctive part of the indexed author name.
    if useful:
        longest = max(useful, key=len)
        preferred = [
            f"AUTHLASTNAME({longest})",
            f"AUTHFIRST({longest})",
        ]
        queries = preferred + [
            q for q in queries if q not in preferred
        ]

    # Remove duplicates while preserving order.
    seen: set[str] = set()
    deduped: list[str] = []

    for query in queries:
        if query in seen:
            continue
        seen.add(query)
        deduped.append(query)

    return deduped[:6]


def search_dsatm_author_directory(
    faculty_name: str,
    limit: int = 15
) -> list[dict[str, Any]]:
    """
    Resolve a typed faculty/researcher name without Excel and without facets.

    Flow:
      AF-ID(60283483)
        + normal Scopus author-name document query
        -> matching DSATM publications
        -> publication author-affiliation data
        -> author name + Scopus Author ID
        -> fuzzy local match
    """

    faculty_name = re.sub(
        r"\s+",
        " ",
        str(faculty_name or "")
    ).strip()

    if len(faculty_name) < 2:
        return []

    global DSATM_AUTHOR_DIRECTORY

    entries_by_eid: dict[str, dict[str, Any]] = {}

    # Search only DSATM papers likely to contain the typed researcher.
    for query_extra in _candidate_query_terms(faculty_name):
        data = _scopus_institution_request(
            start=0,
            count=25,
            query_extra=query_extra,
        )

        results = data.get("search-results") or {}
        entries = list(results.get("entry") or [])

        for entry in entries:
            if not isinstance(entry, dict):
                continue

            key = str(
                entry.get("eid")
                or entry.get("dc:identifier")
                or entry.get("prism:url")
                or ""
            ).strip()

            if not key:
                key = str(id(entry))

            entries_by_eid[key] = entry

        # Once we already have enough likely papers, avoid unnecessary
        # additional Scopus requests.
        if len(entries_by_eid) >= 40:
            break

    # If the normal indexed-name filters returned nothing, try a conservative
    # fallback over the newest DSATM publications.
    if not entries_by_eid:
        data = _scopus_institution_request(
            start=0,
            count=25
        )

        entries = list(
            (data.get("search-results") or {}).get("entry")
            or []
        )

        for entry in entries:
            if not isinstance(entry, dict):
                continue

            key = str(
                entry.get("eid")
                or entry.get("dc:identifier")
                or entry.get("prism:url")
                or ""
            ).strip()

            if key:
                entries_by_eid[key] = entry

    candidates_by_id: dict[str, dict[str, Any]] = {}

    for entry in entries_by_eid.values():

        authors = _get_publication_authors(entry)

        for author in authors:

            author_id = re.sub(
                r"\D",
                "",
                str(author.get("author_id") or "")
            )

            author_name = str(
                author.get("name") or ""
            ).strip()

            if not author_id or not author_name:
                continue

            score = _name_match_score(
                faculty_name,
                author_name
            )

            if score < 55:
                continue

            afids = [
                re.sub(r"\D", "", str(v))
                for v in (author.get("afids") or [])
                if str(v).strip()
            ]

            # If affiliation IDs are explicitly present, prefer authors who are
            # actually attached to DSATM in that publication.
            affiliation_bonus = 0

            if (
                DSATM_SCOPUS_AFFILIATION_ID
                in afids
            ):
                affiliation_bonus = 10

            final_score = min(
                100,
                score + affiliation_bonus
            )

            existing = candidates_by_id.get(
                author_id
            )

            candidate = {
                "name": author_name,
                "author_id": author_id,
                "affiliation":
                    DSATM_INSTITUTION_NAME,
                "city": "Bengaluru",
                "country": "India",
                "documents":
                    int(
                        (existing or {}).get(
                            "documents",
                            0
                        )
                    ) + 1,
                "excel_match": False,
                "score": final_score,
                "source":
                    "DSATM Scopus publication author data",
            }

            if existing:
                candidate["score"] = max(
                    final_score,
                    int(existing.get("score") or 0)
                )

            candidates_by_id[
                author_id
            ] = candidate

            # Keep the reusable in-memory directory updated.
            DSATM_AUTHOR_DIRECTORY[
                author_id
            ] = candidate

    candidates = list(
        candidates_by_id.values()
    )

    candidates.sort(
        key=lambda item: (
            int(item.get("score") or 0),
            int(item.get("documents") or 0),
        ),
        reverse=True,
    )

    return candidates[:limit]


def find_dsatm_authors_by_name(
    faculty_name: str,
    max_records: int = 500
) -> list[dict[str, Any]]:
    return search_dsatm_author_directory(
        faculty_name,
        limit=15
    )



def load_dsatm_author_directory_excel() -> int:
    """
    Load DSATM_Author_Directory.xlsx into the in-memory author directory.

    Expected columns:
      Author Name
      Scopus Author ID
    """

    global DSATM_AUTHOR_DIRECTORY

    if not DSATM_AUTHOR_DIRECTORY_FILE.exists():
        return 0

    try:
        df = pd.read_excel(
            DSATM_AUTHOR_DIRECTORY_FILE,
            engine="openpyxl"
        )

        if (
            "Author Name" not in df.columns
            or "Scopus Author ID" not in df.columns
        ):
            return 0

        loaded = 0

        for _, row in df.iterrows():
            name = str(
                row.get("Author Name")
                or ""
            ).strip()

            author_id = re.sub(
                r"\D",
                "",
                str(
                    row.get("Scopus Author ID")
                    or ""
                )
            )

            if not name or not author_id:
                continue

            DSATM_AUTHOR_DIRECTORY[
                author_id
            ] = {
                "name": name,
                "author_id": author_id,
                "affiliation":
                    DSATM_INSTITUTION_NAME,
                "city": "Bengaluru",
                "country": "India",
                "documents": 0,
                "excel_match": True,
                "score": 100,
                "source":
                    "DSATM_Author_Directory.xlsx",
            }

            loaded += 1

        return loaded

    except Exception as exc:
        print(
            "Unable to load DSATM author directory:",
            exc
        )
        return 0


def save_dsatm_author_directory_excel(
    authors_by_id: dict[str, dict[str, Any]]
) -> Path:
    """
    Save only the two requested columns:
      Author Name
      Scopus Author ID
    """

    rows = []

    for author_id, item in authors_by_id.items():
        name = str(
            item.get("name")
            or ""
        ).strip()

        clean_id = re.sub(
            r"\D",
            "",
            str(author_id or "")
        )

        if not name or not clean_id:
            continue

        rows.append({
            "Author Name": name,
            "Scopus Author ID": clean_id,
        })

    rows.sort(
        key=lambda item:
            item["Author Name"].casefold()
    )

    df = pd.DataFrame(
        rows,
        columns=[
            "Author Name",
            "Scopus Author ID",
        ],
    )

    temp_file = (
        BASE_DIR
        / "DSATM_Author_Directory_temp.xlsx"
    )

    with pd.ExcelWriter(
        temp_file,
        engine="openpyxl"
    ) as writer:
        df.to_excel(
            writer,
            index=False,
            sheet_name="DSATM Authors"
        )

        ws = writer.book[
            "DSATM Authors"
        ]

        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions
        ws.column_dimensions["A"].width = 38
        ws.column_dimensions["B"].width = 22

        for cell in ws[1]:
            cell.font = cell.font.copy(
                bold=True
            )

    temp_file.replace(
        DSATM_AUTHOR_DIRECTORY_FILE
    )

    return DSATM_AUTHOR_DIRECTORY_FILE


def _update_author_directory_progress(**kwargs) -> None:
    with AUTHOR_DIRECTORY_PROGRESS_LOCK:
        AUTHOR_DIRECTORY_PROGRESS.update(kwargs)


def build_dsatm_author_directory(
    max_publications: int = 5000
) -> dict[str, Any]:
    """
    Build DSATM_Author_Directory.xlsx with:
      Author Name
      Scopus Author ID

    Progress is continuously written to AUTHOR_DIRECTORY_PROGRESS so the
    frontend can display a live progress bar.
    """

    global DSATM_AUTHOR_DIRECTORY

    max_publications = max(
        25,
        min(int(max_publications or 5000), 5000)
    )

    _update_author_directory_progress(
        running=True,
        stage="fetching",
        percent=1,
        current=0,
        total=0,
        authors_found=0,
        message="Connecting to Scopus and counting DSATM publications...",
        error="",
        completed=False,
    )

    first = _scopus_institution_request(
        start=0,
        count=25
    )

    results = first.get("search-results") or {}

    try:
        total_results = int(
            results.get("opensearch:totalResults") or 0
        )
    except Exception:
        total_results = 0

    entries = list(results.get("entry") or [])
    limit = min(total_results, max_publications)

    _update_author_directory_progress(
        total=limit,
        current=len(entries),
        percent=2 if limit else 0,
        message=(
            f"DSATM publications found: {total_results}. "
            "Loading publication records..."
        ),
    )

    start_index = len(entries)

    while start_index < limit:
        page = _scopus_institution_request(
            start=start_index,
            count=min(25, limit - start_index)
        )

        page_entries = list(
            (page.get("search-results") or {}).get("entry") or []
        )

        if not page_entries:
            break

        entries.extend(page_entries)
        start_index += len(page_entries)

        fetch_percent = 2
        if limit:
            fetch_percent = min(
                20,
                2 + int(18 * min(start_index, limit) / limit)
            )

        _update_author_directory_progress(
            stage="fetching",
            current=min(start_index, limit),
            total=limit,
            percent=fetch_percent,
            message=(
                f"Loading DSATM publications: "
                f"{min(start_index, limit)} / {limit}"
            ),
        )

    authors_by_id: dict[str, dict[str, Any]] = {}
    documents_processed = 0
    documents_without_author_data = 0

    _update_author_directory_progress(
        stage="authors",
        current=0,
        total=len(entries),
        percent=20,
        message="Extracting author names and Scopus Author IDs...",
    )

    for position, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            continue

        documents_processed += 1

        authors = _get_publication_authors(entry)

        if not authors:
            documents_without_author_data += 1
        else:
            for author in authors:
                author_id = re.sub(
                    r"\D",
                    "",
                    str(author.get("author_id") or "")
                )

                author_name = str(
                    author.get("name") or ""
                ).strip()

                afids = [
                    re.sub(r"\D", "", str(value))
                    for value in (author.get("afids") or [])
                    if str(value).strip()
                ]

                if not author_id or not author_name:
                    continue

                # Prefer authors explicitly linked to DSATM. If the API response
                # omits author-level affiliation IDs, do not discard a usable
                # author ID from a document already scoped to DSATM.
                if (
                    afids
                    and DSATM_SCOPUS_AFFILIATION_ID not in afids
                ):
                    continue

                existing = authors_by_id.get(author_id)

                if existing is None:
                    authors_by_id[author_id] = {
                        "name": author_name,
                        "author_id": author_id,
                        "documents": 1,
                    }
                else:
                    existing["documents"] = (
                        int(existing.get("documents") or 0) + 1
                    )

                    if len(author_name) > len(
                        str(existing.get("name") or "")
                    ):
                        existing["name"] = author_name

        process_percent = 20
        if entries:
            process_percent = min(
                97,
                20 + int(77 * position / len(entries))
            )

        _update_author_directory_progress(
            stage="authors",
            current=position,
            total=len(entries),
            percent=process_percent,
            authors_found=len(authors_by_id),
            message=(
                f"Processing publication {position} / {len(entries)} "
                f"• Authors found: {len(authors_by_id)}"
            ),
        )

    if not authors_by_id:
        raise HTTPException(
            502,
            (
                "Scopus publications were found, but no usable "
                "author names and Scopus Author IDs could be extracted. "
                "The current Elsevier API entitlement may not return "
                "author-affiliation metadata for these records."
            )
        )

    _update_author_directory_progress(
        stage="saving",
        percent=98,
        authors_found=len(authors_by_id),
        message="Creating DSATM_Author_Directory.xlsx...",
    )

    save_dsatm_author_directory_excel(authors_by_id)

    DSATM_AUTHOR_DIRECTORY = {}

    for author_id, item in authors_by_id.items():
        DSATM_AUTHOR_DIRECTORY[author_id] = {
            "name": item["name"],
            "author_id": author_id,
            "affiliation": DSATM_INSTITUTION_NAME,
            "city": "Bengaluru",
            "country": "India",
            "documents": int(item.get("documents") or 0),
            "excel_match": True,
            "score": 100,
            "source": "DSATM_Author_Directory.xlsx",
        }

    result = {
        "success": True,
        "institution": DSATM_INSTITUTION_NAME,
        "affiliation_id": DSATM_SCOPUS_AFFILIATION_ID,
        "scopus_publications": total_results,
        "publications_processed": documents_processed,
        "documents_without_author_data": documents_without_author_data,
        "unique_dsatm_authors": len(authors_by_id),
        "file": DSATM_AUTHOR_DIRECTORY_FILE.name,
    }

    _update_author_directory_progress(
        running=False,
        stage="complete",
        percent=100,
        current=len(entries),
        total=len(entries),
        authors_found=len(authors_by_id),
        message=(
            f"DSATM Author Directory ready • "
            f"{len(authors_by_id)} authors found"
        ),
        error="",
        completed=True,
        result=result,
    )

    return result


def _author_directory_build_worker(max_publications: int) -> None:
    try:
        build_dsatm_author_directory(
            max_publications=max_publications
        )
    except Exception as exc:
        detail = (
            exc.detail
            if isinstance(exc, HTTPException)
            else str(exc)
        )

        _update_author_directory_progress(
            running=False,
            stage="error",
            message="Unable to build DSATM Author Directory.",
            error=str(detail),
            completed=False,
        )


def search_local_dsatm_author_directory(
    faculty_name: str,
    limit: int = 15
) -> list[dict[str, Any]]:
    """
    Search only the locally cached/persisted DSATM author directory.
    """

    faculty_name = re.sub(
        r"\s+",
        " ",
        str(faculty_name or "")
    ).strip()

    if len(faculty_name) < 2:
        return []

    if not DSATM_AUTHOR_DIRECTORY:
        load_dsatm_author_directory_excel()

    candidates = []

    for item in DSATM_AUTHOR_DIRECTORY.values():
        score = _name_match_score(
            faculty_name,
            str(
                item.get("name")
                or ""
            )
        )

        if score < 45:
            continue

        candidate = dict(item)
        candidate["score"] = score

        candidates.append(
            candidate
        )

    candidates.sort(
        key=lambda item: (
            int(
                item.get("score")
                or 0
            ),
            int(
                item.get("documents")
                or 0
            ),
        ),
        reverse=True,
    )

    return candidates[:limit]


def _scopus_authors(entry: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    raw = entry.get("author") or []
    if isinstance(raw, dict):
        raw = [raw]
    names = []
    normalized = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = (item.get("authname") or item.get("ce:indexed-name") or item.get("preferred-name") or "").strip()
        aid = str(item.get("authid") or item.get("@auid") or "").strip()
        if name:
            names.append(name)
        normalized.append({"name": name, "id": aid})
    if not names:
        creator = str(entry.get("dc:creator") or "").strip()
        if creator:
            names = [creator]
    return "; ".join(names), normalized


def _scopus_record(entry: dict[str, Any]) -> dict[str, Any]:
    authors_text, _ = _scopus_authors(entry)
    date = str(entry.get("prism:coverDate") or "")
    doi = str(entry.get("prism:doi") or "")
    try:
        citations = int(entry.get("citedby-count") or 0)
    except Exception:
        citations = 0
    return {
        "title": str(entry.get("dc:title") or "Untitled publication"),
        "authors": authors_text,
        "author_ids": "",
        "year": date[:4] if len(date) >= 4 else "",
        "source": str(entry.get("prism:publicationName") or ""),
        "citations": citations,
        "doi": doi,
        "document_type": str(entry.get("subtypeDescription") or entry.get("subtype") or ""),
        "eid": str(entry.get("eid") or ""),
        "link": f"https://doi.org/{doi}" if doi else str(entry.get("prism:url") or ""),
        "abstract": "",
        "keywords": "",
        "sheet": "Live Scopus",
    }


def get_live_scopus_dashboard(author_id: str, max_records: int = 200) -> dict[str, Any]:
    author_id = re.sub(r"\D", "", str(author_id))
    if not author_id:
        raise HTTPException(400, "Enter a valid numeric Scopus Author ID.")

    first = _scopus_request(author_id, start=0, count=25)
    results = first.get("search-results", {})
    try:
        total_results = int(results.get("opensearch:totalResults") or 0)
    except Exception:
        total_results = 0
    entries = list(results.get("entry") or [])

    start = len(entries)
    limit = min(total_results, max_records)
    while start < limit:
        page = _scopus_request(author_id, start=start, count=min(25, limit - start))
        page_entries = list((page.get("search-results") or {}).get("entry") or [])
        if not page_entries:
            break
        entries.extend(page_entries)
        start += len(page_entries)

    records = [_scopus_record(e) for e in entries]
    faculty_name = ""
    coauthors: set[str] = set()
    for e in entries:
        _, authors = _scopus_authors(e)
        for a in authors:
            if a.get("id") == author_id and a.get("name"):
                faculty_name = a["name"]
            elif a.get("name"):
                coauthors.add(norm(a["name"]))
    if not faculty_name:
        faculty_name = f"Scopus Author {author_id}"

    years: dict[str, int] = {}
    sources: dict[str, int] = {}
    types: dict[str, int] = {}
    source_types = {"Journal": 0, "Conference": 0, "Other": 0}
    total_citations = 0
    for r in records:
        total_citations += r["citations"]
        y = r["year"] or "Unknown"
        years[y] = years.get(y, 0) + 1
        src = r["source"] or "Unknown source"
        sources[src] = sources.get(src, 0) + 1
        typ = r["document_type"] or "Unspecified"
        types[typ] = types.get(typ, 0) + 1
        source_types[classify_source_type(typ)] += 1

    latest_year = max((int(y) for y in years if y.isdigit()), default=None)
    latest_count = years.get(str(latest_year), 0) if latest_year else 0
    payload = {
        "success": True,
        "data_source": "Live Scopus",
        "faculty": faculty_name,
        "faculty_name": faculty_name,
        "department": "Live Scopus profile",
        "scopus_author_id": author_id,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "total_publications_scopus": total_results,
        "returned_publications": len(records),
        "truncated": total_results > len(records),
        "kpis": {
            "publications": total_results,
            "citations": total_citations,
            "h_index": calc_h_index([r["citations"] for r in records]),
            "coauthors": len(coauthors),
            "latest_year": latest_year or "—",
            "latest_year_publications": latest_count,
            "unique_sources": len(sources),
        },
        "by_year": dict(sorted(years.items(), key=lambda x: x[0])),
        "top_sources": dict(sorted(sources.items(), key=lambda x: x[1], reverse=True)[:8]),
        "document_types": types,
        "source_types": source_types,
        "publications": sorted(records, key=lambda r: (r["year"], r["citations"]), reverse=True),
    }
    LIVE_CACHE[author_id] = payload
    return payload

ALIASES = {
    "authors": ["authors", "author names", "author(s)"],
    "author_ids": ["author(s) id", "author ids", "author id", "scopus author id"],
    "authors_affiliations": ["authors with affiliations", "author affiliations", "authors affiliations"],
    "affiliations": ["affiliations", "affiliation"],
    "title": ["title", "document title", "article title"],
    "year": ["year", "publication year"],
    "source": ["source title", "source", "journal", "publication name"],
    "cited_by": ["cited by", "citations", "citation count"],
    "doi": ["doi", "digital object identifier"],
    "document_type": ["document type", "type"],
    "eid": ["eid", "scopus eid"],
    "link": ["link", "url"],
    "abstract": ["abstract"],
    "keywords": ["author keywords", "keywords", "index keywords"],
}

DEPARTMENT_RULES = [
    ("CSE - AI & ML", ["artificial intelligence and machine learning", "ai and ml", "ai & ml", "aiml"]),
    ("CSE - Data Science", ["data science", "cse ds", "computer science and engineering data science"]),
    ("CSE", ["computer science and engineering", "computer science engineering", "department of cse"]),
    ("ISE", ["information science and engineering", "information science engineering"]),
    ("ECE", ["electronics and communication", "electronics & communication"]),
    ("EEE", ["electrical and electronics", "electrical & electronics"]),
    ("Mechanical", ["mechanical engineering"]),
    ("Civil", ["civil engineering"]),
    ("MBA", ["master of business administration", "management studies"]),
    ("Basic Sciences", ["physics", "chemistry", "mathematics", "humanities"]),
]


def norm(text: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(text).strip().lower()).strip()


def infer_mapping(columns) -> dict[str, str | None]:
    cols = list(columns)
    normalized = {norm(c): c for c in cols}
    mapping: dict[str, str | None] = {}
    for key, candidates in ALIASES.items():
        found = None
        for candidate in candidates:
            nc = norm(candidate)
            if nc in normalized:
                found = normalized[nc]
                break
        if found is None:
            for c in cols:
                nc = norm(c)
                if any(norm(candidate) in nc or nc in norm(candidate) for candidate in candidates):
                    found = c
                    break
        mapping[key] = found
    return mapping


def clean_df(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    df = df.dropna(how="all")
    for col in df.columns:
        if df[col].dtype == object:
            df[col] = df[col].fillna("").astype(str).str.strip()
    return df


def read_excel_bytes(content: bytes, filename: str) -> pd.DataFrame:
    suffix = Path(filename).suffix.lower()
    if suffix not in {".xls", ".xlsx"}:
        raise HTTPException(400, "Please upload an Excel file (.xls or .xlsx).")
    try:
        engine = "xlrd" if suffix == ".xls" else "openpyxl"
        book = pd.ExcelFile(io.BytesIO(content), engine=engine)
        frames = []
        for sheet in book.sheet_names:
            part = pd.read_excel(book, sheet_name=sheet)
            part = clean_df(part)
            if not part.empty:
                part["_sheet"] = sheet
                frames.append(part)
        if not frames:
            raise HTTPException(400, "The workbook does not contain readable data.")
        largest = max(frames, key=len)
        same_schema = [f for f in frames if set(f.columns) == set(largest.columns)]
        return pd.concat(same_schema, ignore_index=True) if same_schema else largest
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, f"Unable to read Excel file: {exc}") from exc


def author_blob(row: pd.Series, mapping: dict[str, str | None]) -> str:
    fields = []
    for key in ("authors_affiliations", "authors", "affiliations"):
        col = mapping.get(key)
        if col and col in row.index:
            fields.append(str(row[col]))
    return "; ".join(fields)


def extract_faculty(df: pd.DataFrame, mapping: dict[str, str | None], institution_keyword: str) -> list[str]:
    inst = norm(institution_keyword)
    names: set[str] = set()
    aff_col = mapping.get("authors_affiliations")
    if aff_col:
        for value in df[aff_col].fillna("").astype(str):
            for segment in re.split(r"\s*;\s*", value):
                if inst and inst in norm(segment):
                    candidate = segment.split(",", 1)[0].strip()
                    if 2 <= len(candidate) <= 100 and any(ch.isalpha() for ch in candidate):
                        names.add(candidate)
    if not names and mapping.get("authors"):
        col = mapping["authors"]
        for value in df[col].fillna("").astype(str):
            for candidate in re.split(r"\s*;\s*", value):
                candidate = candidate.strip()
                if 2 <= len(candidate) <= 100 and any(ch.isalpha() for ch in candidate):
                    names.add(candidate)
    return sorted(names, key=str.casefold)


def filter_faculty(df: pd.DataFrame, mapping: dict[str, str | None], faculty: str) -> pd.DataFrame:
    target = norm(faculty)
    if not target:
        return df.iloc[0:0].copy()
    mask = df.apply(lambda r: target in norm(author_blob(r, mapping)), axis=1)
    return df[mask].copy()


def faculty_department(df: pd.DataFrame, mapping: dict[str, str | None], faculty: str, institution: str) -> str:
    target, inst = norm(faculty), norm(institution)
    text_parts: list[str] = []
    aff_col = mapping.get("authors_affiliations")
    if aff_col:
        for value in df[aff_col].fillna("").astype(str):
            for segment in re.split(r"\s*;\s*", value):
                ns = norm(segment)
                if target in ns and (not inst or inst in ns):
                    text_parts.append(ns)
    if not text_parts and mapping.get("affiliations"):
        text_parts = [norm(v) for v in df[mapping["affiliations"]].fillna("").astype(str)]
    blob = " ".join(text_parts)
    for label, keys in DEPARTMENT_RULES:
        if any(norm(k) in blob for k in keys):
            return label
    return "Other / Not Detected"


def safe_int_series(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").fillna(0).astype(int)


def record(row: pd.Series, mapping: dict[str, str | None]) -> dict[str, Any]:
    def val(key: str, default=""):
        col = mapping.get(key)
        if not col or col not in row.index:
            return default
        v = row[col]
        return "" if pd.isna(v) else str(v)

    try:
        citations_num = int(float(val("cited_by", "0")))
    except Exception:
        citations_num = 0
    year = val("year", "")
    try:
        year = str(int(float(year)))
    except Exception:
        pass
    doi = val("doi")
    link = val("link") or (f"https://doi.org/{doi}" if doi else "")
    return {
        "title": val("title", "Untitled publication"), "authors": val("authors"),
        "author_ids": val("author_ids"), "year": year, "source": val("source"),
        "citations": citations_num, "doi": doi, "document_type": val("document_type"),
        "eid": val("eid"), "link": link, "abstract": val("abstract"),
        "keywords": val("keywords"), "sheet": str(row.get("_sheet", "")),
    }


def calc_h_index(citations: list[int]) -> int:
    vals = sorted((max(0, int(c)) for c in citations), reverse=True)
    h = 0
    for i, c in enumerate(vals, 1):
        if c >= i:
            h = i
        else:
            break
    return h


def split_authors(text: str) -> list[str]:
    return [x.strip() for x in re.split(r"\s*;\s*", text or "") if x.strip()]


def detect_author_id(records: list[dict], faculty: str) -> str:
    target = norm(faculty)
    ids: list[str] = []
    for r in records:
        authors, author_ids = split_authors(r.get("authors", "")), split_authors(r.get("author_ids", ""))
        if len(authors) == len(author_ids):
            for a, aid in zip(authors, author_ids):
                if target in norm(a) or norm(a) in target:
                    ids.append(aid)
    return Counter(ids).most_common(1)[0][0] if ids else "Not available in export"


def coauthor_count(records: list[dict], faculty: str) -> int:
    target = norm(faculty)
    people = set()
    for r in records:
        for a in split_authors(r.get("authors", "")):
            na = norm(a)
            if na and target not in na and na not in target:
                people.add(na)
    return len(people)


def classify_source_type(doc_type: str) -> str:
    n = norm(doc_type)
    if "conference" in n:
        return "Conference"
    if any(k in n for k in ("article", "review", "journal", "letter", "editorial")):
        return "Journal"
    return "Other"


def faculty_metadata(df, mapping, faculty, institution):
    f = filter_faculty(df, mapping, faculty)
    citation_col = mapping.get("cited_by")
    citations = int(safe_int_series(f[citation_col]).sum()) if citation_col else 0

    # Map the faculty name to the Scopus Author ID already present in the
    # uploaded Excel (Author ID / Author(s) ID / Scopus Author ID).
    # Scopus exports normally keep Authors and Author(s) ID in the same order.
    records = [record(row, mapping) for _, row in f.iterrows()]
    scopus_author_id = detect_author_id(records, faculty) if mapping.get("author_ids") else "Not available in export"

    return {
        "faculty": faculty,
        "department": faculty_department(f, mapping, faculty, institution),
        "publications": len(f),
        "citations": citations,
        "scopus_author_id": scopus_author_id,
    }


def institution_overall(df: pd.DataFrame, mapping: dict[str, str | None], meta: list[dict]) -> dict[str, Any]:
    citation_col = mapping.get("cited_by")
    total_citations = int(safe_int_series(df[citation_col]).sum()) if citation_col else 0

    year_counts: dict[str, int] = {}
    year_col = mapping.get("year")
    if year_col:
        for raw in df[year_col].fillna(""):
            try:
                y = str(int(float(raw)))
            except Exception:
                y = "Unknown"
            year_counts[y] = year_counts.get(y, 0) + 1

    doc_counts = {"Journal": 0, "Conference": 0, "Other": 0}
    doc_col = mapping.get("document_type")
    if doc_col:
        for value in df[doc_col].fillna("").astype(str):
            doc_counts[classify_source_type(value)] += 1
    else:
        doc_counts["Other"] = len(df)

    source_col = mapping.get("source")
    unique_sources = int(df[source_col].fillna("").astype(str).replace("", pd.NA).nunique()) if source_col else 0
    valid_years = [int(y) for y in year_counts if y.isdigit()]
    latest_year = max(valid_years) if valid_years else None

    dept_counts = Counter(m.get("department") or "Other / Not Detected" for m in meta)
    top_faculty = sorted(meta, key=lambda x: (x.get("publications", 0), x.get("citations", 0)), reverse=True)

    return {
        "total_faculty": len(meta),
        "total_records": len(df),
        "total_citations": total_citations,
        "journal_count": doc_counts["Journal"],
        "conference_count": doc_counts["Conference"],
        "other_count": doc_counts["Other"],
        "unique_sources": unique_sources,
        "department_count": len(dept_counts),
        "latest_year": latest_year or "—",
        "top_faculty": top_faculty[0]["faculty"] if top_faculty else "—",
        "by_year": dict(sorted(year_counts.items(), key=lambda x: x[0])),
        "department_faculty_counts": dict(sorted(dept_counts.items(), key=lambda x: x[1], reverse=True)),
    }



# =========================================================
# AUTO LOAD DSATM AUTHOR DIRECTORY
# =========================================================
_loaded_author_count = load_dsatm_author_directory_excel()
print(
    "DSATM author directory loaded:",
    _loaded_author_count,
    "authors"
)


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={}
    )


@app.get("/api/health")
def health():
    return {"status": "ok", "loaded": STATE["df"] is not None, "filename": STATE["filename"]}



@app.post("/api/upload")
async def upload_excel(
    file: UploadFile = File(...),
    institution_keyword: str = Form(
        "Dayananda Sagar Academy of Technology and Management"
    )
):
    global faculty_meta_cache

    # -------------------------------------------------------
    # 1. READ EXCEL FILE
    # -------------------------------------------------------

    content = await file.read()

    df = read_excel_bytes(
        content,
        file.filename or "upload.xlsx"
    )

    # -------------------------------------------------------
    # 2. DETECT SCOPUS COLUMNS
    # -------------------------------------------------------

    mapping = infer_mapping(df.columns)

    if not mapping.get("title") or not (
        mapping.get("authors")
        or mapping.get("authors_affiliations")
    ):
        raise HTTPException(
            400,
            "Could not detect required Scopus columns. "
            "The sheet should contain Title and "
            "Authors/Authors with affiliations."
        )

    # -------------------------------------------------------
    # 3. EXTRACT FACULTY
    # -------------------------------------------------------

    faculty = extract_faculty(
        df,
        mapping,
        institution_keyword
    )

    # -------------------------------------------------------
    # 4. CREATE FACULTY METADATA
    # -------------------------------------------------------

    meta = [
        faculty_metadata(
            df,
            mapping,
            f,
            institution_keyword
        )
        for f in faculty
    ]

    # -------------------------------------------------------
    # 5. IMPORTANT:
    # CACHE FACULTY NAME <-> SCOPUS AUTHOR ID
    # -------------------------------------------------------

    faculty_meta_cache = meta

    print(
        f"Faculty cache loaded: "
        f"{len(faculty_meta_cache)} faculty"
    )

    # Optional debug output
    for item in faculty_meta_cache[:5]:
        print(
            "Faculty:",
            item.get("faculty"),
            "| Scopus ID:",
            item.get("scopus_author_id")
        )

    # -------------------------------------------------------
    # 6. DEPARTMENTS
    # -------------------------------------------------------

    departments = sorted({
        m["department"]
        for m in meta
    })

    # -------------------------------------------------------
    # 7. INSTITUTION OVERALL
    # -------------------------------------------------------

    overall = institution_overall(
        df,
        mapping,
        meta
    )

    # -------------------------------------------------------
    # 8. SAVE APPLICATION STATE
    # -------------------------------------------------------

    STATE.update({
        "df": df,
        "filename": file.filename,
        "institution_keyword":
            institution_keyword.strip(),
        "mapping": mapping,
        "faculty_meta": meta,
        "departments": departments,
        "overall": overall
    })

    # -------------------------------------------------------
    # 9. RETURN RESULT TO GUI
    # -------------------------------------------------------

    return {
        "message": "Excel loaded successfully",

        "filename": file.filename,

        "rows": len(df),

        "faculty_count": len(faculty),

        "faculty": faculty,

        "faculty_meta": meta,

        "departments": departments,

        "overall": overall,

        "detected_columns": mapping
    }

@app.get("/api/dashboard")
def dashboard(faculty: str):
    df = STATE.get("df")
    if df is None:
        raise HTTPException(400, "Upload an Excel file first.")
    mapping = STATE["mapping"]
    filtered = filter_faculty(df, mapping, faculty)
    records = [record(row, mapping) for _, row in filtered.iterrows()]
    years, sources, types, source_types = {}, {}, {}, {"Journal": 0, "Conference": 0, "Other": 0}
    total_citations = 0
    for r in records:
        total_citations += r["citations"]
        y = r["year"] or "Unknown"; years[y] = years.get(y, 0) + 1
        src = r["source"] or "Unknown source"; sources[src] = sources.get(src, 0) + 1
        typ = r["document_type"] or "Unspecified"; types[typ] = types.get(typ, 0) + 1
        source_types[classify_source_type(typ)] += 1
    years_sorted = dict(sorted(years.items(), key=lambda x: x[0]))
    top_sources = dict(sorted(sources.items(), key=lambda x: x[1], reverse=True)[:8])
    latest_year = max((int(y) for y in years if y.isdigit()), default=None)
    latest_count = years.get(str(latest_year), 0) if latest_year else 0
    citation_values = [r["citations"] for r in records]
    return {
        "faculty": faculty,
        "department": faculty_department(filtered, mapping, faculty, STATE["institution_keyword"]),
        "scopus_author_id": detect_author_id(records, faculty),
        "kpis": {"publications": len(records), "citations": total_citations, "h_index": calc_h_index(citation_values), "coauthors": coauthor_count(records, faculty), "latest_year": latest_year or "—", "latest_year_publications": latest_count, "unique_sources": len(sources)},
        "by_year": years_sorted, "top_sources": top_sources, "document_types": types, "source_types": source_types,
        "publications": sorted(records, key=lambda r: (r["year"], r["citations"]), reverse=True),
    }


@app.get("/api/summary")
def summary(department: str = ""):
    df = STATE.get("df")
    if df is None:
        raise HTTPException(400, "Upload an Excel file first.")

    # IMPORTANT: use metadata cached during upload instead of rescanning the
    # whole workbook for every faculty member on every button click.
    rows = list(STATE.get("faculty_meta") or [])
    if department:
        rows = [r for r in rows if r.get("department") == department]
    rows.sort(key=lambda x: (x.get("publications", 0), x.get("citations", 0)), reverse=True)

    overall = dict(STATE.get("overall") or {})
    if department:
        overall["total_faculty"] = len(rows)
        overall["top_faculty"] = rows[0]["faculty"] if rows else "—"
        # This value is a faculty-publication association count and can include
        # the same paper more than once when multiple faculty co-author it.
        overall["faculty_publication_links"] = sum(r.get("publications", 0) for r in rows)

    return {
        "rows": rows,
        "total_faculty": overall.get("total_faculty", len(rows)),
        "total_records": overall.get("total_records", len(df)),
        "total_citations": overall.get("total_citations", 0),
        "journal_count": overall.get("journal_count", 0),
        "conference_count": overall.get("conference_count", 0),
        "other_count": overall.get("other_count", 0),
        "unique_sources": overall.get("unique_sources", 0),
        "department_count": overall.get("department_count", 0),
        "latest_year": overall.get("latest_year", "—"),
        "top_faculty": overall.get("top_faculty", "—"),
        "by_year": overall.get("by_year", {}),
        "department_faculty_counts": overall.get("department_faculty_counts", {}),
        "faculty_publication_links": overall.get("faculty_publication_links"),
        "departments": STATE.get("departments", []),
    }



@app.get("/api/scopus/test")
def api_scopus_test():

    response = requests.get(
        SCOPUS_SEARCH_URL,
        headers=scopus_headers(),
        params={
            "query": "TITLE(test)",
            "count": 1
        },
        timeout=20,
    )

    if not response.ok:
        raise HTTPException(
            response.status_code,
            response.text[:800]
        )

    return {
        "success": True,
        "status_code":
            response.status_code,
        "message":
            "Scopus API connection successful"
    }


@app.get("/api/scopus/institution-search")
def api_scopus_institution_search():

    data = _scopus_institution_request(
        start=0,
        count=5
    )

    results = (
        data.get(
            "search-results"
        )
        or {}
    )

    entries = list(
        results.get("entry")
        or []
    )

    return {
        "success": True,
        "institution":
            DSATM_INSTITUTION_NAME,
        "affiliation_id":
            DSATM_SCOPUS_AFFILIATION_ID,
        "total_results":
            results.get(
                "opensearch:totalResults"
            ),
        "returned_records":
            len(entries),
        "entries":
            entries,
    }


@app.get("/api/scopus/build-author-directory")
def api_build_dsatm_author_directory(
    max_publications: int = 5000
):
    """
    Synchronous/manual build endpoint. The GUI uses the background endpoint.
    """
    return build_dsatm_author_directory(
        max_publications=max_publications
    )


@app.post("/api/scopus/start-author-directory-build")
def api_start_author_directory_build(
    max_publications: int = 5000,
    refresh: bool = False
):
    """
    Start author-directory creation in the background.
    """

    if (
        DSATM_AUTHOR_DIRECTORY_FILE.exists()
        and not refresh
    ):
        loaded = load_dsatm_author_directory_excel()

        return {
            "success": True,
            "started": False,
            "already_ready": True,
            "authors_loaded": loaded,
            "message": "DSATM Author Directory is already ready.",
        }

    with AUTHOR_DIRECTORY_PROGRESS_LOCK:
        if AUTHOR_DIRECTORY_PROGRESS.get("running"):
            return {
                "success": True,
                "started": False,
                "already_running": True,
                "progress": dict(AUTHOR_DIRECTORY_PROGRESS),
            }

        AUTHOR_DIRECTORY_PROGRESS.update({
            "running": True,
            "stage": "starting",
            "percent": 0,
            "current": 0,
            "total": 0,
            "authors_found": 0,
            "message": "Starting DSATM Author Directory build...",
            "error": "",
            "completed": False,
        })

    thread = threading.Thread(
        target=_author_directory_build_worker,
        args=(max_publications,),
        daemon=True,
        name="dsatm-author-directory-builder",
    )
    thread.start()

    return {
        "success": True,
        "started": True,
        "message": "DSATM Author Directory build started.",
    }


@app.get("/api/scopus/author-directory-progress")
def api_author_directory_progress():
    with AUTHOR_DIRECTORY_PROGRESS_LOCK:
        progress = dict(AUTHOR_DIRECTORY_PROGRESS)

    progress["directory_ready"] = (
        DSATM_AUTHOR_DIRECTORY_FILE.exists()
    )
    progress["directory_file"] = (
        DSATM_AUTHOR_DIRECTORY_FILE.name
    )
    progress["cached_authors"] = len(
        DSATM_AUTHOR_DIRECTORY
    )

    return progress


@app.get("/api/scopus/author-directory-status")
def api_author_directory_status():
    if (
        DSATM_AUTHOR_DIRECTORY_FILE.exists()
        and not DSATM_AUTHOR_DIRECTORY
    ):
        load_dsatm_author_directory_excel()

    with AUTHOR_DIRECTORY_PROGRESS_LOCK:
        running = bool(
            AUTHOR_DIRECTORY_PROGRESS.get("running")
        )

    return {
        "success": True,
        "directory_ready":
            DSATM_AUTHOR_DIRECTORY_FILE.exists(),
        "running":
            running,
        "file":
            DSATM_AUTHOR_DIRECTORY_FILE.name,
        "authors_loaded":
            len(DSATM_AUTHOR_DIRECTORY),
        "source":
            "DSATM_Author_Directory.xlsx",
    }


@app.get("/api/scopus/download-author-directory")
def api_download_dsatm_author_directory():

    if not DSATM_AUTHOR_DIRECTORY_FILE.exists():
        raise HTTPException(
            404,
            (
                "DSATM_Author_Directory.xlsx has not been "
                "created yet."
            )
        )

    file_bytes = (
        DSATM_AUTHOR_DIRECTORY_FILE.read_bytes()
    )

    return StreamingResponse(
        io.BytesIO(file_bytes),
        media_type=(
            "application/vnd.openxmlformats-"
            "officedocument.spreadsheetml.sheet"
        ),
        headers={
            "Content-Disposition":
                'attachment; filename="DSATM_Author_Directory.xlsx"'
        },
    )


@app.get("/api/scopus/institution-authors")
def api_scopus_institution_authors():

    if not DSATM_AUTHOR_DIRECTORY:
        load_dsatm_author_directory_excel()

    rows = sorted(
        DSATM_AUTHOR_DIRECTORY.values(),
        key=lambda item:
            str(item.get("name") or "").casefold(),
    )

    return {
        "success": True,
        "institution":
            DSATM_INSTITUTION_NAME,
        "affiliation_id":
            DSATM_SCOPUS_AFFILIATION_ID,
        "cached_authors":
            len(rows),
        "directory_file":
            DSATM_AUTHOR_DIRECTORY_FILE.name,
        "file_exists":
            DSATM_AUTHOR_DIRECTORY_FILE.exists(),
        "authors":
            rows,
    }


@app.get("/api/scopus/author-search")
def api_scopus_author_search(
    name: str,
    institution: str = ""
):

    name = re.sub(
        r"\s+",
        " ",
        str(name or "")
    ).strip()

    if len(name) < 2:
        raise HTTPException(
            400,
            "Enter at least 2 characters of the faculty name."
        )

    if not DSATM_AUTHOR_DIRECTORY_FILE.exists():
        raise HTTPException(
            503,
            (
                "DSATM_Author_Directory.xlsx is missing from the project root. "
                "Copy the supplied file beside app.py and restart the server."
            )
        )

    candidates = (
        search_local_dsatm_author_directory(
            name,
            limit=15
        )
    )

    return {
        "success": True,
        "query":
            name,
        "institution":
            DSATM_INSTITUTION_NAME,
        "affiliation_id":
            DSATM_SCOPUS_AFFILIATION_ID,
        "total_results":
            len(candidates),
        "candidates":
            candidates,
        "source":
            "DSATM_Author_Directory.xlsx",
        "directory_ready":
            DSATM_AUTHOR_DIRECTORY_FILE.exists(),
        "cached_authors":
            len(
                DSATM_AUTHOR_DIRECTORY
            ),
    }


@app.get("/api/scopus/author/{author_id}")
def api_scopus_author(author_id: str):

    try:

        # --------------------------------------------------
        # CLEAN AUTHOR ID
        # --------------------------------------------------

        clean_author_id = re.sub(
            r"\D",
            "",
            str(author_id)
        )

        if not clean_author_id:
            raise HTTPException(
                400,
                "Enter a valid Scopus Author ID."
            )

        # --------------------------------------------------
        # GET LIVE PUBLICATIONS FROM SCOPUS
        # --------------------------------------------------

        paginated = get_all_publications_by_author_id(
            clean_author_id,
            max_records=500
        )

        entries = paginated.get(
            "publications",
            []
        )

        total_publications = int(
            paginated.get(
                "total_results",
                len(entries)
            ) or 0
        )

        publications = []

        total_citations = 0

        # --------------------------------------------------
        # PROCESS PUBLICATIONS
        # --------------------------------------------------

        for item in entries:

            try:
                citations = int(
                    item.get(
                        "citedby-count",
                        0
                    ) or 0
                )
            except Exception:
                citations = 0

            total_citations += citations

            cover_date = item.get(
                "prism:coverDate",
                ""
            )

            doi = item.get(
                "prism:doi",
                ""
            )

            publications.append({

                "title":
                    item.get(
                        "dc:title",
                        "N/A"
                    ),

                "authors":
                    item.get(
                        "dc:creator",
                        "N/A"
                    ),

                "year":
                    cover_date[:4]
                    if cover_date
                    else "",

                "date":
                    cover_date,

                "source":
                    item.get(
                        "prism:publicationName",
                        "N/A"
                    ),

                "doi":
                    doi,

                "citations":
                    citations,

                "document_type":
                    item.get(
                        "subtypeDescription",
                        ""
                    ),

                "scopus_id":
                    item.get(
                        "dc:identifier",
                        ""
                    ),

                "eid":
                    item.get(
                        "eid",
                        ""
                    ),

                "link":
                    (
                        f"https://doi.org/{doi}"
                        if doi
                        else item.get(
                            "prism:url",
                            ""
                        )
                    )
            })

        # --------------------------------------------------
        # TOTAL PUBLICATIONS
        # --------------------------------------------------

        # total_publications is already supplied by the paginated Scopus helper.
        if total_publications < len(publications):
            total_publications = len(publications)

        # ==================================================
        # MATCH SCOPUS ID WITH EXCEL FACULTY
        # ==================================================

        faculty_name = None
        department = None

        print(
            "Searching faculty cache for Scopus ID:",
            clean_author_id
        )

        print(
            "Faculty cache size:",
            len(faculty_meta_cache)
        )

        for row in faculty_meta_cache:

            cached_id = str(
                row.get(
                    "scopus_author_id",
                    ""
                )
            ).strip()

            # Remove any accidental spaces / non-digits
            cached_id_clean = re.sub(
                r"\D",
                "",
                cached_id
            )

            if (
                cached_id_clean
                ==
                clean_author_id
            ):

                faculty_name = row.get(
                    "faculty"
                )

                department = row.get(
                    "department"
                )

                print(
                    "MATCH FOUND:",
                    faculty_name,
                    clean_author_id
                )

                break

        # --------------------------------------------------
        # FALLBACK
        # --------------------------------------------------

        if not faculty_name:

            faculty_name = (
                f"Scopus Author "
                f"{clean_author_id}"
            )

            department = (
                "Live Scopus profile"
            )

            print(
                "No Excel faculty match found for:",
                clean_author_id
            )

        # ==================================================
        # ANALYTICS
        # ==================================================

        years = {}

        sources = {}

        source_types = {
            "Journal": 0,
            "Conference": 0,
            "Other": 0
        }

        citation_values = []

        for pub in publications:

            citation_values.append(
                pub["citations"]
            )

            year = (
                pub["year"]
                or "Unknown"
            )

            years[year] = (
                years.get(
                    year,
                    0
                ) + 1
            )

            source = (
                pub["source"]
                or "Unknown source"
            )

            sources[source] = (
                sources.get(
                    source,
                    0
                ) + 1
            )

            source_type = classify_source_type(
                pub["document_type"]
            )

            source_types[source_type] += 1

        # --------------------------------------------------
        # LATEST YEAR
        # --------------------------------------------------

        valid_years = [
            int(y)
            for y in years
            if str(y).isdigit()
        ]

        latest_year = (
            max(valid_years)
            if valid_years
            else None
        )

        latest_year_count = (
            years.get(
                str(latest_year),
                0
            )
            if latest_year
            else 0
        )

        # --------------------------------------------------
        # TOP SOURCES
        # --------------------------------------------------

        top_sources = dict(
            sorted(
                sources.items(),
                key=lambda x: x[1],
                reverse=True
            )[:8]
        )

        # ==================================================
        # RESPONSE FOR GUI
        # ==================================================

        response = {

            "success": True,

            "data_source":
                "Live Scopus",

            "author_id":
                clean_author_id,

            "scopus_author_id":
                clean_author_id,

            # THIS IS WHAT app.js READS
            "faculty_name":
                faculty_name,

            "faculty":
                faculty_name,

            "department":
                department,

            "total_publications":
                total_publications,

            "total_publications_scopus":
                total_publications,

            "returned_publications":
                len(publications),

            "total_citations_for_returned_records":
                total_citations,

            "truncated":
                bool(paginated.get("truncated"))
                if "paginated" in locals()
                else total_publications > len(publications),

            "kpis": {

                "publications":
                    total_publications,

                "citations":
                    total_citations,

                # Local h-index because Author Retrieval
                # API is not authorized
                "h_index":
                    calc_h_index(
                        citation_values
                    ),

                "coauthors":
                    0,

                "latest_year":
                    latest_year
                    or "—",

                "latest_year_publications":
                    latest_year_count,

                "unique_sources":
                    len(sources)
            },

            "by_year":
                dict(
                    sorted(
                        years.items(),
                        key=lambda x: x[0]
                    )
                ),

            "top_sources":
                top_sources,

            "source_types":
                source_types,

            "publications":
                sorted(
                    publications,
                    key=lambda r: (
                        r.get(
                            "year",
                            ""
                        ),
                        r.get(
                            "citations",
                            0
                        )
                    ),
                    reverse=True
                )
        }

        # --------------------------------------------------
        # SAVE LIVE RESULT
        # --------------------------------------------------

        LIVE_CACHE[
            clean_author_id
        ] = response

        return response

    except HTTPException:
        raise

    except Exception as e:

        return {
            "success": False,
            "error": str(e)
        }



# =========================================================
# GITHUB MASTER EXCEL PERSISTENCE
# =========================================================

def _github_settings() -> dict[str, str]:
    settings = {
        "token": os.getenv("GITHUB_TOKEN", "").strip(),
        "owner": os.getenv("GITHUB_OWNER", "shreenidhibs").strip(),
        "repo": os.getenv("GITHUB_REPO", "DSATM-Scopus-AI").strip(),
        "branch": os.getenv("GITHUB_BRANCH", "main").strip(),
        "excel_path": os.getenv(
            "GITHUB_EXCEL_PATH",
            "DSATM Scopus Master.xlsx"
        ).strip(),
    }

    missing = [
        key for key in ("token", "owner", "repo", "branch", "excel_path")
        if not settings.get(key)
    ]

    if missing:
        raise HTTPException(
            500,
            "Missing GitHub configuration: " + ", ".join(missing)
        )

    return settings


def _github_headers() -> dict[str, str]:
    cfg = _github_settings()
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {cfg['token']}",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _github_content_url(path: str) -> str:
    cfg = _github_settings()
    encoded_path = quote(path, safe="/")
    return (
        f"https://api.github.com/repos/"
        f"{cfg['owner']}/{cfg['repo']}/contents/{encoded_path}"
    )


def _github_existing_file(path: str) -> dict[str, Any] | None:
    cfg = _github_settings()

    response = requests.get(
        _github_content_url(path),
        headers=_github_headers(),
        params={"ref": cfg["branch"]},
        timeout=30,
    )

    if response.status_code == 404:
        return None

    if not response.ok:
        try:
            detail = response.json()
        except Exception:
            detail = response.text[:1000]

        raise HTTPException(
            502,
            f"GitHub file lookup failed ({response.status_code}): {detail}"
        )

    data = response.json()

    if not isinstance(data, dict):
        raise HTTPException(
            502,
            "GitHub returned an unexpected response for the master Excel file."
        )

    return data


def commit_excel_bytes_to_github(
    excel_bytes: bytes,
    *,
    path: str | None = None,
    commit_message: str | None = None,
) -> dict[str, Any]:
    """
    Create or replace the configured Excel file in GitHub.

    GitHub requires the current blob SHA when replacing an existing file.
    """

    cfg = _github_settings()
    target_path = (path or cfg["excel_path"]).strip()

    existing = _github_existing_file(target_path)

    payload: dict[str, Any] = {
        "message": commit_message or (
            "Auto-update DSATM Scopus master from Live Scopus"
        ),
        "content": base64.b64encode(excel_bytes).decode("ascii"),
        "branch": cfg["branch"],
    }

    if existing and existing.get("sha"):
        payload["sha"] = existing["sha"]

    response = requests.put(
        _github_content_url(target_path),
        headers=_github_headers(),
        json=payload,
        timeout=60,
    )

    if response.status_code not in (200, 201):
        try:
            detail = response.json()
        except Exception:
            detail = response.text[:1500]

        raise HTTPException(
            502,
            f"GitHub update failed ({response.status_code}): {detail}"
        )

    data = response.json()
    commit = data.get("commit") or {}
    content = data.get("content") or {}

    return {
        "success": True,
        "path": target_path,
        "branch": cfg["branch"],
        "repository": f"{cfg['owner']}/{cfg['repo']}",
        "commit_sha": commit.get("sha", ""),
        "commit_url": commit.get("html_url", ""),
        "file_url": content.get("html_url", ""),
        "created": response.status_code == 201,
    }


@app.get("/api/github/update-status")
def github_update_status():
    """Check whether GitHub persistence is configured without exposing the token."""
    cfg = {
        "token_set": bool(os.getenv("GITHUB_TOKEN", "").strip()),
        "owner": os.getenv("GITHUB_OWNER", "shreenidhibs").strip(),
        "repo": os.getenv("GITHUB_REPO", "DSATM-Scopus-AI").strip(),
        "branch": os.getenv("GITHUB_BRANCH", "main").strip(),
        "excel_path": os.getenv(
            "GITHUB_EXCEL_PATH",
            "DSATM Scopus Master.xlsx"
        ).strip(),
    }
    return {
        "success": True,
        **cfg,
    }


@app.get("/api/scopus/refresh-excel")
def refresh_excel_from_scopus(max_records_per_author: int = 500):
    """Build an updated Excel workbook using live Scopus data for all detected faculty.

    Workflow:
    1. User uploads the institutional Scopus Excel export.
    2. Faculty names and Scopus Author IDs are detected from that workbook.
    3. This endpoint queries Scopus for every valid Author ID.
    4. A new XLSX is returned with live faculty metrics and live publications.

    The original uploaded dataset is not overwritten on the server.
    """
    if STATE.get("df") is None:
        raise HTTPException(400, "Upload and analyze the institutional Excel file first.")

    faculty_rows = list(STATE.get("faculty_meta") or [])
    if not faculty_rows:
        raise HTTPException(400, "No faculty records were detected in the uploaded Excel file.")

    max_records_per_author = max(25, min(int(max_records_per_author or 500), 1000))
    metrics_rows: list[dict[str, Any]] = []
    publication_rows: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []

    for item in faculty_rows:
        faculty = str(item.get("faculty") or "").strip()
        department = str(item.get("department") or "").strip()
        raw_id = str(item.get("scopus_author_id") or "").strip()
        author_id = re.sub(r"\D", "", raw_id)

        if not author_id:
            failures.append({
                "Faculty": faculty,
                "Scopus Author ID": raw_id,
                "Status": "Skipped - no valid Scopus Author ID detected",
            })
            continue

        try:
            live = get_live_scopus_dashboard(author_id, max_records=max_records_per_author)
            k = live.get("kpis") or {}
            publications = list(live.get("publications") or [])

            metrics_rows.append({
                "Faculty Name": faculty or live.get("faculty") or f"Scopus Author {author_id}",
                "Department": department,
                "Scopus Author ID": author_id,
                "Live Publications": int(k.get("publications") or 0),
                "Live Citations": int(k.get("citations") or 0),
                "Live h-index": int(k.get("h_index") or 0),
                "Unique Co-authors": int(k.get("coauthors") or 0),
                "Latest Publication Year": k.get("latest_year") or "",
                "Publications in Latest Year": int(k.get("latest_year_publications") or 0),
                "Unique Sources": int(k.get("unique_sources") or 0),
                "Records Retrieved": int(live.get("returned_publications") or len(publications)),
                "Scopus Total Results": int(live.get("total_publications_scopus") or 0),
                "Truncated": "Yes" if live.get("truncated") else "No",
                "Updated At": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "Status": "Updated",
            })

            for pub in publications:
                publication_rows.append({
                    "Faculty Name": faculty or live.get("faculty") or f"Scopus Author {author_id}",
                    "Department": department,
                    "Scopus Author ID": author_id,
                    "Title": pub.get("title", ""),
                    "Authors": pub.get("authors", ""),
                    "Year": pub.get("year", ""),
                    "Source": pub.get("source", ""),
                    "Citations": pub.get("citations", 0),
                    "Document Type": pub.get("document_type", ""),
                    "DOI": pub.get("doi", ""),
                    "EID": pub.get("eid", ""),
                    "Link": pub.get("link", ""),
                    "Updated At": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                })

        except HTTPException as exc:
            failures.append({
                "Faculty": faculty,
                "Scopus Author ID": author_id,
                "Status": f"Scopus API error {exc.status_code}: {str(exc.detail)[:300]}",
            })
        except Exception as exc:
            failures.append({
                "Faculty": faculty,
                "Scopus Author ID": author_id,
                "Status": f"Error: {str(exc)[:300]}",
            })

    if not metrics_rows and failures:
        first_error = failures[0].get("Status", "Unable to refresh faculty data")
        raise HTTPException(502, first_error)

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        # Preserve a copy of the parsed institutional export for traceability.
        original = STATE["df"].copy()
        original.to_excel(writer, index=False, sheet_name="Original Scopus Export")
        pd.DataFrame(metrics_rows).to_excel(writer, index=False, sheet_name="Faculty Live Metrics")
        pd.DataFrame(publication_rows).to_excel(writer, index=False, sheet_name="Live Publications")
        if failures:
            pd.DataFrame(failures).to_excel(writer, index=False, sheet_name="Refresh Issues")

        # Lightweight formatting for usability.
        for ws in writer.book.worksheets:
            ws.freeze_panes = "A2"
            ws.auto_filter.ref = ws.dimensions
            for cell in ws[1]:
                cell.font = cell.font.copy(bold=True)
            for column_cells in ws.columns:
                letter = column_cells[0].column_letter
                max_len = 0
                for cell in column_cells[:200]:
                    value = "" if cell.value is None else str(cell.value)
                    max_len = max(max_len, len(value))
                ws.column_dimensions[letter].width = min(max(max_len + 2, 10), 45)

    output.seek(0)
    excel_bytes = output.getvalue()

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    github_result = commit_excel_bytes_to_github(
        excel_bytes,
        commit_message=(
            f"Auto-update DSATM Scopus master - {stamp}"
        ),
    )

    return {
        "success": True,
        "message": "Live Scopus refresh completed and master Excel committed to GitHub.",
        "updated_faculty": len(metrics_rows),
        "live_publications": len(publication_rows),
        "issues": len(failures),
        "updated_at": stamp,
        "github": github_result,
        "vercel_note": (
            "If this GitHub repository is connected to the Vercel project, "
            "the commit will trigger a new deployment automatically."
        ),
    }


@app.get("/api/scopus/export/{author_id}")
def export_live_scopus(author_id: str):
    author_id = re.sub(r"\D", "", str(author_id))
    data = LIVE_CACHE.get(author_id) or get_live_scopus_dashboard(author_id)
    rows = data.get("publications", [])
    output = io.BytesIO()
    pd.DataFrame(rows).to_excel(output, index=False, sheet_name="Live Scopus Publications", engine="openpyxl")
    output.seek(0)
    headers = {"Content-Disposition": f'attachment; filename="scopus_{author_id}_live_publications.xlsx"'}
    return StreamingResponse(output, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers=headers)


@app.get("/api/export")
def export_faculty(faculty: str):
    df = STATE.get("df")
    if df is None:
        raise HTTPException(400, "Upload an Excel file first.")
    filtered = filter_faculty(df, STATE["mapping"], faculty)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        filtered.to_excel(writer, index=False, sheet_name="Publications")
    output.seek(0)
    safe_name = re.sub(r"[^A-Za-z0-9_-]+", "_", faculty).strip("_") or "faculty"
    headers = {"Content-Disposition": f'attachment; filename="{safe_name}_scopus_publications.xlsx"'}
    return StreamingResponse(output, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers=headers)
