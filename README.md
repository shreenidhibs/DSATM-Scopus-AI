# DSATM Faculty Scopus Research Intelligence Dashboard

## Project Overview

The **DSATM Faculty Scopus Research Intelligence Dashboard** is a
web-based research analytics application developed for **Dayananda Sagar
Academy of Technology and Management (DSATM)**.

It combines institutional Scopus Excel data with the **Elsevier Scopus
API** to provide faculty-wise and institution-level publication and
citation analytics. The system supports Excel analysis, live Scopus
retrieval using Author IDs, institutional synchronization, publication
deduplication, and generation of a permanent master workbook.

## Main Features

-   DSATM-branded research dashboard
-   Upload and analyze Scopus `.xls` and `.xlsx` files
-   Automatic detection of Scopus columns
-   Automatic faculty extraction
-   Faculty-to-Scopus Author ID mapping
-   Department-wise faculty organization
-   Faculty publication dashboard
-   Institution research summary
-   Live Scopus publication retrieval
-   Scopus Author Search
-   Citation and H-index analysis
-   Year-wise publication trends
-   Journal / Conference / Other classification
-   Top publication source analysis
-   Institution-wide Scopus synchronization
-   New publication detection
-   Existing publication updates
-   New DSATM author detection
-   Publication deduplication
-   Synchronization progress tracking
-   Updated Excel master export

## Technology Stack

### Backend

-   Python
-   FastAPI
-   Uvicorn
-   Pandas
-   Requests
-   OpenPyXL
-   python-dotenv

### Frontend

-   HTML
-   CSS
-   JavaScript
-   Jinja2 Templates

### External Service

-   Elsevier Scopus API

## Project Structure

``` text
faculty_scopus_dashboard_live/
├── app.py
├── .env
├── requirements.txt
├── README.md
├── services/
│   └── scopus_service.py
├── templates/
│   └── index.html
├── static/
│   ├── css/
│   ├── js/
│   └── images/
├── DSATM Scopus.xls
└── DSATM Scopus Master.xlsx
```

## Application Workflow

``` text
DSATM Scopus Excel
        ↓
Upload / Auto Load
        ↓
Detect Scopus Columns
        ↓
Extract Faculty + Scopus Author IDs
        ↓
Faculty / Institution Dashboard
        ↓
Live Scopus API
        ↓
Institution Synchronization
        ↓
DSATM Scopus Master_temp.xlsx
        ↓
DSATM Scopus Master.xlsx
```

## Scopus API Configuration

Create a `.env` file in the project root:

``` env
ELS_API_KEY=your_scopus_api_key_here
ELS_INST_TOKEN=
```

`ELS_API_KEY` is required. `ELS_INST_TOKEN` is optional.

Never commit the real API key to GitHub.

Recommended `.gitignore`:

``` gitignore
.env
.venv/
__pycache__/
*.pyc
DSATM Scopus Master_temp.xlsx
```

## Installation

### 1. Open the project directory

``` bash
cd faculty_scopus_dashboard_live
```

### 2. Create and activate a virtual environment

macOS/Linux:

``` bash
python3 -m venv .venv
source .venv/bin/activate
```

Windows:

``` bash
python -m venv .venv
.venv\Scripts\activate
```

### 3. Install dependencies

``` bash
pip install -r requirements.txt
```

Core dependencies can also be installed with:

``` bash
pip install fastapi uvicorn pandas requests python-dotenv openpyxl xlrd python-multipart jinja2
```

## Check the Code

Before starting the application:

``` bash
python -m py_compile app.py
```

No output means the Python file compiled successfully.

## Run the Application

``` bash
uvicorn app:app --reload
```

Then open the local address displayed by Uvicorn, normally:

``` text
http://127.0.0.1:8000
```

## Excel Data

The system supports `.xls` and `.xlsx` Scopus exports and attempts to
detect fields such as:

-   Authors
-   Author(s) ID
-   Authors with affiliations
-   Affiliations
-   Title
-   Year
-   Source title
-   Cited by
-   DOI
-   Document type
-   EID
-   Link
-   Abstract
-   Keywords

## Default Institutional Data

Original institutional export:

``` text
DSATM Scopus.xls
```

Permanent synchronized workbook:

``` text
DSATM Scopus Master.xlsx
```

When the master workbook exists, it can be used as the preferred data
source. Otherwise, the original institutional Scopus file is used.

## Faculty Dashboard

The faculty dashboard can provide:

-   Faculty name
-   Department
-   Scopus Author ID
-   Total publications
-   Citations
-   H-index
-   Co-authors
-   Latest publication year
-   Latest-year publication count
-   Unique publication sources
-   Year-wise trends
-   Top sources
-   Document types
-   Publication details

## Institution Summary

Institution-level analytics can include:

-   Total faculty
-   Total publication records
-   Total citations
-   Journal publications
-   Conference publications
-   Other publications
-   Unique sources
-   Departments
-   Latest publication year
-   Top faculty
-   Department-wise distribution
-   Year-wise publication trends

## Live Scopus Mode

Live Scopus mode retrieves publication information using a Scopus Author
ID.

Retrieved publication information can include title, year, source, DOI,
citation count, document type, Scopus document identifier, EID, and
publication link.

## Scopus Author Search

The application provides author-search support to help identify the
correct Scopus Author ID. Candidate ranking can consider name
similarity, affiliation, document count, and IDs already detected from
the institutional Excel data.

Faculty identity should be verified when multiple Scopus profiles or
similar author names exist.

## Institution Synchronization

The **Refresh Excel from Live Scopus** workflow:

1.  Retrieves DSATM documents from Scopus.
2.  Processes institutional publication records.
3.  Extracts publication identifiers.
4.  Retrieves available author/affiliation metadata.
5.  Matches institutional authors.
6.  Compares live records with the existing master dataset.
7.  Updates existing publications.
8.  Adds new publications.
9.  Detects new institutional authors.
10. Deduplicates publication records.
11. Generates synchronization summary data.
12. Writes a temporary workbook.
13. Replaces the permanent master only after successful writing.
14. Reloads the application state.
15. Returns the updated workbook.

## Publication Deduplication

Preferred identifiers are:

1.  Scopus EID
2.  DOI
3.  Scopus document identifier
4.  Title + publication year as fallback

This helps prevent duplicate publication records.

## Safe Master Workbook Update

Synchronization first writes to:

``` text
DSATM Scopus Master_temp.xlsx
```

After successful writing, the temporary file replaces:

``` text
DSATM Scopus Master.xlsx
```

The replacement step is:

``` python
temp_master_file.replace(MASTER_EXCEL_FILE)
```

This reduces the chance of an incomplete write directly damaging the
permanent master workbook.

## Important API Endpoints

``` text
GET  /
GET  /api/health
POST /api/upload
GET  /api/dashboard
GET  /api/summary
GET  /api/scopus/test
GET  /api/scopus/author-search
GET  /api/scopus/author/{author_id}
```

The project also contains institution synchronization and
synchronization-progress functionality used by the dashboard.

## Common Problems

### Missing API Key

Make sure `.env` contains:

``` env
ELS_API_KEY=your_actual_api_key
```

Restart Uvicorn after changing the environment file.

### Bad CRC-32 Excel Error

If you see:

``` text
Bad CRC-32 for file 'xl/workbook.xml'
```

the workbook may be damaged or incomplete. Open it in Excel and save it
again as a valid `.xlsx` workbook.

### Master Workbook Not Found

If you see an error that `DSATM Scopus Master.xlsx` does not exist,
verify that the temporary workbook is replaced after successful writing:

``` python
temp_master_file.replace(MASTER_EXCEL_FILE)
```

### Port Already in Use

On macOS/Linux:

``` bash
lsof -i :8000
```

Or run on another port:

``` bash
uvicorn app:app --reload --port 8001
```

### Syntax / Indentation Error

Run:

``` bash
python -m py_compile app.py
```

Fix the reported line before restarting the server.

## Data Accuracy

Research metrics depend on Scopus metadata and correct faculty-to-author
mapping. Faculty with multiple Scopus profiles, name variations, changed
affiliations, or incomplete metadata may require manual verification.

Official institutional reports should be reviewed before final use.

## Future Enhancements

-   Scopus Author Profile API integration
-   Better author-profile disambiguation
-   ORCID integration
-   Department-specific dashboards
-   Collaboration network visualization
-   Advanced citation analytics
-   Automated faculty research reports
-   Scheduled synchronization
-   PDF research report generation
-   Role-based administrator access
-   Database-backed publication history
-   Cloud deployment
-   Custom institutional domain deployment

## Purpose

The project provides a centralized DSATM research intelligence platform
for monitoring faculty publications, analyzing citation performance,
identifying new Scopus records, maintaining a synchronized master
research dataset, and supporting institutional research reporting.

## Institution

**Dayananda Sagar Academy of Technology and Management (DSATM)**\
Faculty Research Analytics / Scopus Intelligence Project

## Disclaimer

Scopus and Elsevier are trademarks or services of their respective
owners. This project uses the Elsevier Scopus API for academic research
analytics. API availability and returned information depend on Elsevier
permissions, subscriptions, institutional access, and usage limits.

This dashboard is an institutional analytics application and is not an
official Elsevier or Scopus product.
