"""
Project-wide path constants for texashydro-db.

All paths anchor to PROJECT_ROOT via __file__ resolution, so scripts work
regardless of the current working directory. Import from any script under
Python/scripts/ as:

    from config import PROJECT_ROOT, INBOX, CSV_EXPORT, DB_PATH

When the project relocates (laptop -> \\\\beg-share\\radon\\), nothing here
needs to change — PROJECT_ROOT is derived from the location of this file.
"""
from pathlib import Path

# Project layout:
#   PROJECT_ROOT/
#   ├── Python/
#   │   └── scripts/
#   │       ├── config.py       <-- this file
#   │       ├── validate.py
#   │       └── ingest_submission.py
#   ├── SQL/
#   ├── templates/
#   ├── submissions/{inbox,processed,rejected}/
#   ├── archive/csv_export/
#   ├── rd8_files_inbox/
#   ├── raw/
#   ├── needs_attention/
#   ├── backups/
#   ├── logs/
#   └── radon.db

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# --- Templates ---
TEMPLATES     = PROJECT_ROOT / "templates"
TEMPLATE_FILE = TEMPLATES / "submission_template.xlsx"

# --- Submission pipeline (xlsx workbooks) ---
SUBMISSIONS = PROJECT_ROOT / "submissions"
INBOX       = SUBMISSIONS / "inbox"
PROCESSED   = SUBMISSIONS / "processed"
REJECTED    = SUBMISSIONS / "rejected"

# --- Instrument-file pipeline (.rd8, .ft) ---
RD8_INBOX       = PROJECT_ROOT / "rd8_files_inbox"
RAW             = PROJECT_ROOT / "raw"
NEEDS_ATTENTION = PROJECT_ROOT / "needs_attention"

# --- Intermediate + output ---
ARCHIVE    = PROJECT_ROOT / "archive"
CSV_EXPORT = ARCHIVE / "csv_export"

# --- Runtime state ---
DB_PATH = PROJECT_ROOT / 'SQL/texashydro.db'
LOGS    = PROJECT_ROOT / "logs"
BACKUPS = PROJECT_ROOT / "backups"