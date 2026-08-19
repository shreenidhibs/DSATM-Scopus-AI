from __future__ import annotations

import io
import os
import re
from datetime import datetime
from collections import Counter
from pathlib import Path
from typing import Any

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


def scopus_headers() -> dict[str, str]:
    api_key = os.getenv("ELS_API_KEY", "").strip()
    if not api_key:
        raise HTTPException(500, "ELS_API_KEY is missing. Add it to the .env file in the project root.")
    headers = {"X-ELS-APIKey": api_key, "Accept": "application/json"}
    inst_token = os.getenv("ELS_INST_TOKEN", "").strip()
    if inst_token:
        headers["X-ELS-Insttoken"] = inst_token
    return headers


def _scopus_request(author_id: str, start: int = 0, count: int = 25) -> dict[str, Any]:
    # Current Scopus service level allows a maximum of 25 records per request.
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
        raise HTTPException(response.status_code, f"Scopus API error: {detail}")
    return response.json()


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


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


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
        params={"query": "TITLE(test)", "count": 1},
        timeout=20,
    )
    if not response.ok:
        raise HTTPException(response.status_code, response.text[:800])
    return {"success": True, "status_code": response.status_code, "message": "Scopus API connection successful"}

@app.get("/api/scopus/author-search")
def api_scopus_author_search(name: str, institution: str = ""):
    """Search Scopus authors by name and return candidates for user confirmation."""
    name = re.sub(r"\s+", " ", str(name or "")).strip()
    if len(name) < 2:
        raise HTTPException(400, "Enter at least 2 characters of the faculty name.")

    # Author Search API syntax. AFFIL is optional and helps narrow common names.
    query = f'AUTHLASTNAME({name.split()[-1]})'
    response = requests.get(
        "https://api.elsevier.com/content/search/author",
        headers=scopus_headers(),
        params={"query": query, "count": 25, "start": 0},
        timeout=30,
    )
    if not response.ok:
        raise HTTPException(response.status_code, f"Scopus Author Search error: {response.text[:800]}")

    results = response.json().get("search-results", {})
    entries = list(results.get("entry") or [])
    wanted = norm(name)
    institution_norm = norm(institution)
    candidates = []

    for e in entries:
        preferred = e.get("preferred-name") or {}
        indexed = str(preferred.get("ce:indexed-name") or e.get("dc:title") or "").strip()
        given = str(preferred.get("ce:given-name") or "").strip()
        surname = str(preferred.get("ce:surname") or "").strip()
        display = indexed or " ".join(x for x in (given, surname) if x).strip()
        author_id = str(e.get("dc:identifier") or "").replace("AUTHOR_ID:", "").strip()
        affiliation = e.get("affiliation-current") or {}
        if isinstance(affiliation, list):
            affiliation = affiliation[0] if affiliation else {}
        affiliation_name = str(affiliation.get("affiliation-name") or "").strip() if isinstance(affiliation, dict) else ""
        city = str(affiliation.get("affiliation-city") or "").strip() if isinstance(affiliation, dict) else ""
        country = str(affiliation.get("affiliation-country") or "").strip() if isinstance(affiliation, dict) else ""
        try:
            documents = int(e.get("document-count") or 0)
        except Exception:
            documents = 0

        # Ranking only; never silently select a same-name author.
        display_norm = norm(display)
        score = 0
        if wanted == display_norm:
            score += 100
        elif wanted in display_norm or display_norm in wanted:
            score += 60
        name_tokens = set(wanted.split())
        score += 8 * len(name_tokens & set(display_norm.split()))
        if institution_norm and institution_norm in norm(affiliation_name):
            score += 50

        # Prefer exact faculty ID already present in uploaded Excel, when available.
        excel_match = None
        for row in faculty_meta_cache:
            if norm(row.get("faculty", "")) == wanted:
                excel_id = re.sub(r"\D", "", str(row.get("scopus_author_id") or ""))
                if excel_id and excel_id == re.sub(r"\D", "", author_id):
                    score += 200
                    excel_match = True
                    break

        candidates.append({
            "name": display or f"Scopus Author {author_id}",
            "author_id": author_id,
            "affiliation": affiliation_name,
            "city": city,
            "country": country,
            "documents": documents,
            "excel_match": bool(excel_match),
            "score": score,
        })

    candidates = [c for c in candidates if c["author_id"]]
    candidates.sort(key=lambda c: (c["score"], c["documents"]), reverse=True)
    return {
        "success": True,
        "query": name,
        "total_results": int(results.get("opensearch:totalResults") or 0),
        "candidates": candidates[:15],
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
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    headers = {
        "Content-Disposition": f'attachment; filename="DSATM_Scopus_Live_Updated_{stamp}.xlsx"',
        "X-Scopus-Updated-Faculty": str(len(metrics_rows)),
        "X-Scopus-Refresh-Issues": str(len(failures)),
    }
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=headers,
    )


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
