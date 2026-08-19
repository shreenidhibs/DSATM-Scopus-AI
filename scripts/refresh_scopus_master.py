from __future__ import annotations

import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
MASTER = ROOT / "DSATM Scopus Master.xlsx"
SEARCH_URL = "https://api.elsevier.com/content/search/scopus"
AFFILIATION_ID = "60283483"
INSTITUTION = "Dayananda Sagar Academy of Technology and Management"


def headers() -> dict[str, str]:
    api_key = os.getenv("ELS_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("ELS_API_KEY GitHub Actions secret is missing.")

    result = {
        "X-ELS-APIKey": api_key,
        "Accept": "application/json",
    }

    inst_token = os.getenv("ELS_INST_TOKEN", "").strip()
    if inst_token:
        result["X-ELS-Insttoken"] = inst_token

    return result


def clean(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and pd.isna(value):
        return ""
    return str(value).strip()


def scopus_doc_id(entry: dict[str, Any]) -> str:
    value = clean(entry.get("dc:identifier"))
    return value.replace("SCOPUS_ID:", "").strip()


def scopus_link(entry: dict[str, Any]) -> str:
    links = entry.get("link") or []
    if isinstance(links, dict):
        links = [links]
    for link in links:
        if not isinstance(link, dict):
            continue
        if clean(link.get("@ref")).lower() == "scopus":
            return clean(link.get("@href"))
    return clean(entry.get("prism:url"))


def affiliation_text(entry: dict[str, Any]) -> str:
    values: list[str] = []
    affiliations = entry.get("affiliation") or []
    if isinstance(affiliations, dict):
        affiliations = [affiliations]
    for aff in affiliations:
        if not isinstance(aff, dict):
            continue
        name = clean(aff.get("affilname"))
        city = clean(aff.get("affiliation-city"))
        country = clean(aff.get("affiliation-country"))
        parts = [x for x in (name, city, country) if x]
        if parts:
            values.append(", ".join(parts))
    return "; ".join(dict.fromkeys(values))


def row_key(row: pd.Series) -> str:
    for col in ("Scopus EID", "EID"):
        if col in row.index:
            value = clean(row.get(col))
            if value:
                return "EID:" + value.lower()

    if "Scopus Document ID" in row.index:
        value = re.sub(r"\D", "", clean(row.get("Scopus Document ID")))
        if value:
            return "SID:" + value

    if "DOI" in row.index:
        value = clean(row.get("DOI")).lower()
        if value:
            return "DOI:" + value

    title = re.sub(r"\s+", " ", clean(row.get("Title"))).lower()
    year = clean(row.get("Year"))
    return f"TY:{title}|{year}"


def entry_key(entry: dict[str, Any]) -> str:
    eid = clean(entry.get("eid"))
    if eid:
        return "EID:" + eid.lower()

    sid = re.sub(r"\D", "", scopus_doc_id(entry))
    if sid:
        return "SID:" + sid

    doi = clean(entry.get("prism:doi")).lower()
    if doi:
        return "DOI:" + doi

    title = re.sub(r"\s+", " ", clean(entry.get("dc:title"))).lower()
    year = clean(entry.get("prism:coverDate"))[:4]
    return f"TY:{title}|{year}"


def get_all_institution_entries() -> tuple[list[dict[str, Any]], int]:
    entries: list[dict[str, Any]] = []
    start = 0
    total = None

    while total is None or start < total:
        response = requests.get(
            SEARCH_URL,
            headers=headers(),
            params={
                "query": f"AF-ID({AFFILIATION_ID})",
                "start": start,
                "count": 25,
                "sort": "-coverDate",
                "view": "STANDARD",
            },
            timeout=45,
        )

        if not response.ok:
            raise RuntimeError(
                f"Scopus institution search failed ({response.status_code}): "
                f"{response.text[:800]}"
            )

        data = response.json()
        results = data.get("search-results") or {}

        if total is None:
            try:
                total = int(results.get("opensearch:totalResults") or 0)
            except Exception:
                total = 0
            print(f"Scopus institution publications reported: {total}", flush=True)

        page = [x for x in (results.get("entry") or []) if isinstance(x, dict)]
        if not page:
            break

        entries.extend(page)
        start += len(page)
        print(f"Fetched {min(start, total or start)} / {total or '?'} publications", flush=True)

        # Be polite to the API while still keeping the workflow quick.
        time.sleep(0.12)

    return entries, int(total or len(entries))


def apply_entry(df: pd.DataFrame, index: int, entry: dict[str, Any]) -> None:
    cover_date = clean(
    entry.get("prism:coverDate")
    )

    # ---------------------------------------------
    # Safe numeric conversion
    # ---------------------------------------------

    values = {
            "Title": clean(entry.get("dc:title")),
            "Year": cover_date[:4],
            "Publication Date": cover_date,
            "Source title": clean(entry.get("prism:publicationName")),
            "Cited by": clean(entry.get("citedby-count")) or "0",
            "DOI": clean(entry.get("prism:doi")),
            "Link": scopus_link(entry),
            "Affiliations": affiliation_text(entry),
            "Document Type": clean(entry.get("subtypeDescription")) or clean(entry.get("subtype")),
            "Source": clean(entry.get("prism:aggregationType")),
            "EID": clean(entry.get("eid")),
            "Scopus EID": clean(entry.get("eid")),
            "Scopus Document ID": scopus_doc_id(entry),
        }

    for col, value in values.items():
        if col not in df.columns:
            df[col] = ""
        # Citation count and identifiers should always track Scopus. Other
        # descriptive fields update only when Scopus returned a nonblank value.
        if col in {"Cited by", "EID", "Scopus EID", "Scopus Document ID"} or value:
            df.at[index, col] = value


def new_row(columns: list[str], entry: dict[str, Any]) -> dict[str, Any]:
    row = {col: "" for col in columns}
    creator = clean(entry.get("dc:creator"))
    cover_date = clean(entry.get("prism:coverDate"))
    affiliations = affiliation_text(entry)
    try:
        year_value = (
            int(cover_date[:4])
            if cover_date[:4]
            else ""
        )
    except (ValueError, TypeError):
        year_value = ""

    try:
        citation_value = int(
            clean(
                entry.get("citedby-count")
            )
            or 0
        )
    except (ValueError, TypeError):
        citation_value = 0

    row.update({
        "Authors": creator,
        "Author full names": creator,
        "Author(s) ID": "",
        "Title": clean(entry.get("dc:title")),
        "Year": year_value,
        "Publication Date": cover_date,
        "Source title": clean(entry.get("prism:publicationName")),
       "Cited by": citation_value,
        "DOI": clean(entry.get("prism:doi")),
        "Link": scopus_link(entry),
        "Affiliations": affiliations,
        "Authors with affiliations": (
            f"{creator}, {affiliations}" if creator and affiliations else creator
        ),
        "Document Type": clean(entry.get("subtypeDescription")) or clean(entry.get("subtype")),
        "Publication Stage": "Final",
        "Source": clean(entry.get("prism:aggregationType")),
        "EID": clean(entry.get("eid")),
        "Scopus EID": clean(entry.get("eid")),
        "Scopus Document ID": scopus_doc_id(entry),
    })
    return row


def main() -> None:
    if not MASTER.exists():
        raise RuntimeError(f"Master workbook not found: {MASTER.name}")

    print(f"Loading {MASTER.name}", flush=True)
    df = pd.read_excel(
    MASTER,
    sheet_name=0,
    engine="openpyxl"
)

    # =========================================================
    # MAKE MASTER DATAFRAME SAFE FOR SCOPUS STRING/NUMERIC UPDATE
    # =========================================================
    #
    # Scopus may return Year, citation counts and document IDs as
    # strings. Existing Excel columns may have been inferred by
    # pandas as int64. Converting the DataFrame to object prevents
    # errors such as:
    #
    # TypeError: Invalid value '2027' for dtype 'int64'
    #
    df = df.astype("object")

    df = df.where(
        pd.notna(df),
        ""
    )

    # Scopus cover date powers the Institution Summary month-wise trend.
    if "Publication Date" not in df.columns:
        df["Publication Date"] = ""

    existing_count = len(df)
    print(f"Existing master publications: {existing_count}", flush=True)

    entries, scopus_total = get_all_institution_entries()

    key_to_index: dict[str, int] = {}
    for idx, row in df.iterrows():
        key = row_key(row)
        if key and key not in key_to_index:
            key_to_index[key] = idx

    updated = 0
    new_rows: list[dict[str, Any]] = []

    for entry in entries:
        key = entry_key(entry)
        existing_index = key_to_index.get(key)

        if existing_index is not None:
            old_citations = (
                clean(df.at[existing_index, "Cited by"])
                if "Cited by" in df.columns
                else ""
            )

            new_citations = clean(entry.get("citedby-count")) or "0"

            apply_entry(
                df,
                existing_index,
                entry
            )

            if old_citations != new_citations:
                updated += 1

            continue

        row = new_row(
            list(df.columns),
            entry
        )

        new_rows.append(row)

        key_to_index[key] = (
            len(df)
            + len(new_rows)
            - 1
        )

    if new_rows:
        df = pd.concat(
            [
                df,
                pd.DataFrame(
                    new_rows,
                    columns=df.columns
                )
            ],
            ignore_index=True
        )
        

    # Final authoritative de-duplication.
    seen: set[str] = set()
    keep: list[int] = []
    for idx, row in df.iterrows():
        key = row_key(row)
        if key in seen:
            continue
        seen.add(key)
        keep.append(idx)
    df = df.loc[keep].reset_index(drop=True)

    summary = pd.DataFrame([{
        "Institution": INSTITUTION,
        "Scopus Affiliation ID": AFFILIATION_ID,
        "Existing Excel Publications": existing_count,
        "Current Scopus Publications": scopus_total,
        "New Publications Added": len(new_rows),
        "Existing Citation Counts Updated": updated,
        "Final Master Publications": len(df),
        "Synced At": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }])

    temp = ROOT / "DSATM Scopus Master_temp.xlsx"
    with pd.ExcelWriter(temp, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Scopus Master Export")
        summary.to_excel(writer, index=False, sheet_name="Institution Sync Summary")

        for ws in writer.book.worksheets:
            ws.freeze_panes = "A2"
            ws.auto_filter.ref = ws.dimensions
            for cell in ws[1]:
                cell.font = cell.font.copy(bold=True)
            for column_cells in ws.columns:
                letter = column_cells[0].column_letter
                max_len = 0
                for cell in column_cells[:250]:
                    value = "" if cell.value is None else str(cell.value)
                    max_len = max(max_len, len(value))
                ws.column_dimensions[letter].width = min(max(max_len + 2, 10), 48)

    temp.replace(MASTER)

    print("--------------------------------------------", flush=True)
    print(f"Scopus total: {scopus_total}", flush=True)
    print(f"New publications added: {len(new_rows)}", flush=True)
    print(f"Citation counts updated: {updated}", flush=True)
    print(f"Final master publications: {len(df)}", flush=True)
    print(f"Saved: {MASTER.name}", flush=True)
    print("--------------------------------------------", flush=True)


if __name__ == "__main__":
    main()
