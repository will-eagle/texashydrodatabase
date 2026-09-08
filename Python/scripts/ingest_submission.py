"""
Ingest validated submission CSVs into radon.db.

Consumes CSVs produced by validate.py (in <project>/archive/csv_export/<stem>/),
loads them into the schema in FK order inside a per-submission transaction, and
archives the source workbook to submissions/processed/YYYY/MM/ on success.
Backs up the database once before the batch.

Two modes:

    # Scan mode (default; matches the operator batch-file workflow):
    python ingest_submission.py
        -> looks at every <archive/csv_export>/<stem>/ dir where the
           corresponding <submissions/inbox>/<stem>.xlsx still exists.
        -> Each such pair is a not-yet-ingested submission.

    # Single-submission mode (dev / testing):
    python ingest_submission.py path/to/csv_export/<stem>/

Exit codes:
    0 = all pending submissions ingested (or nothing pending)
    1 = at least one submission failed and was rolled back
    2 = pre-flight error (DB missing, etc.)

Time zone:
    validate.py leaves datetimes as local Texas strings ('YYYY-MM-DD HH:MM').
    This script converts them to UTC ISO 8601 before insert, satisfying the
    schema's *_utc columns. 
TODO:
    - Add a `submissions` audit table so every ingested row can be traced back
      to a submitter + workbook. Currently only the ingest log holds that link.
    - Add a `file_keys` table and load into it instead of dumping to CSV.
    - Old-row protection: updates to rows older than 30 days should go to a
      review queue rather than applying silently (design doc).
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

import config as cfg


LOCAL_TZ = ZoneInfo("America/Chicago")
UTC = ZoneInfo("UTC")


# =============================================================================
# Column mappings
# =============================================================================

# Template CSV column  ->  DB column
RENAME = {
    "visits": {
        "datetime_start": "datetime_start_utc",
        "datetime_end":   "datetime_end_utc",
    },
    "samples": {
        "datetime_collected": "datetime_collection_utc",
    },
    "field_measurements": {
        "datetime_measured": "datetime_utc",
    },
}

# Columns holding LOCAL Texas time in the CSV. Named by their DB column name
# (i.e. post-rename), since prepare_frame renames before it converts.
LOCAL_DATETIME_COLS = {
    "visits":              ["datetime_start_utc", "datetime_end_utc"],
    "samples":             ["datetime_collection_utc"],
    "field_measurements":  ["datetime_utc"],
}

# Which columns the DB accepts per table. Anything else in the CSV is dropped
# silently so the inserts stay clean if the template picks up extra cols later.
DB_COLUMNS = {
    "campaigns": [
        "campaign_id", "project_id", "name", "description", "lead",
        "start_date", "end_date", "notes",
    ],
    "sites": [
        "site_id", "site_type", "latitude", "longitude", "elevation_m",
        "coord_crs", "aquifer", "county", "notes",
    ],
    "visits": [
        "visit_id", "site_id", "campaign_id", "datetime_start_utc",
        "datetime_end_utc", "personnel", "weather", "flow_condition",
        "fieldnotes_filename", "notes",
    ],
    "samples": [
        "sample_id", "visit_id", "datetime_collection_utc", "sample_type",
        "intended_analysis", "replicate", "container", "filtered",
        "parent_sample_id", "notes",
    ],
    "field_measurements": [
        "meas_id", "visit_id", "parameter", "value", "unit", "uncertainty",
        "datetime_utc", "method", "inst_id", "notes",
    ],
}

# INSERT strategy per table:
#   "or_ignore" -- new rows added, existing rows silently kept (definitional
#                  tables where a submitter might redeclare an existing site)
#   "strict"    -- PK collision is a hard error (data rows, must be unique)
INSERT_STRATEGY = {
    "campaigns":          "or_ignore",
    "sites":              "or_ignore",
    "visits":             "strict",
    "samples":            "strict",
    "field_measurements": "strict",
}

# FK-safe insert order.
INGEST_ORDER = ["campaigns", "sites", "visits", "samples", "field_measurements"]


# =============================================================================
# Helpers
# =============================================================================

def local_to_utc(s) -> str | None:
    """
    Parse a local Texas datetime string and return UTC ISO 8601 with 'Z'.
    Accepts 'YYYY-MM-DD HH:MM' (template convention) and 'YYYY-MM-DD HH:MM:SS'
    (what pandas produces when Excel stored a cell as a native datetime).
    """
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return None
    s = str(s).strip()
    if not s or s.lower() == "nan":
        return None
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            dt_local = datetime.strptime(s, fmt).replace(tzinfo=LOCAL_TZ)
            return dt_local.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            continue
    raise ValueError(f"could not parse datetime: {s!r}")


def backup_db(db_path: Path) -> Path:
    """Copy the current DB into backups/<timestamp>/ and return the new path."""
    cfg.BACKUPS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest_dir = cfg.BACKUPS / stamp
    dest_dir.mkdir()
    dest = dest_dir / db_path.name
    shutil.copy2(db_path, dest)
    return dest


def read_submission_info(csv_dir: Path) -> dict:
    """Return the submission_info key-value pairs, or empty dict."""
    path = csv_dir / "submission_info.csv"
    if not path.exists():
        return {}
    df = pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[""])
    if not {"field", "value"}.issubset(df.columns):
        return {}
    return {
        str(r["field"]).strip(): (str(r["value"]).strip() if pd.notna(r["value"]) else "")
        for _, r in df.iterrows() if pd.notna(r["field"])
    }


def load_csv(csv_dir: Path, sheet: str) -> pd.DataFrame | None:
    path = csv_dir / f"{sheet}.csv"
    if not path.exists():
        return None
    df = pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[""])
    return df


def prepare_frame(sheet: str, df: pd.DataFrame | None) -> pd.DataFrame | None:
    """Rename template cols to DB cols, convert local datetimes to UTC, drop
    columns the schema doesn't have, and drop fully-empty rows."""
    if df is None or df.empty:
        return df
    if sheet in RENAME:
        df = df.rename(columns=RENAME[sheet])
    for col in LOCAL_DATETIME_COLS.get(sheet, []):
        if col in df.columns:
            df[col] = df[col].apply(local_to_utc)
    keep = [c for c in DB_COLUMNS[sheet] if c in df.columns]
    df = df[keep]
    df = df.dropna(how="all")
    return df


def insert_frame(conn: sqlite3.Connection, sheet: str, df: pd.DataFrame | None) -> tuple[int, int]:
    """Insert rows; returns (inserted, seen)."""
    if df is None or df.empty:
        return 0, 0
    strategy = INSERT_STRATEGY[sheet]
    verb = "INSERT OR IGNORE" if strategy == "or_ignore" else "INSERT"
    cols = list(df.columns)
    placeholders = ",".join("?" for _ in cols)
    sql = f'{verb} INTO {sheet} ({",".join(cols)}) VALUES ({placeholders})'
    rows = [
        tuple(None if pd.isna(v) else v for v in row)
        for row in df.itertuples(index=False, name=None)
    ]
    cur = conn.executemany(sql, rows)
    return cur.rowcount, len(rows)


def dump_file_keys(csv_dir: Path, submission_stem: str) -> Path | None:
    """
    Persist file_key rows to archive/file_keys/<stem>_file_key.csv so the
    instrument-file ingester can look up filename -> sample_id later.
    (Interim solution until there's a proper file_keys table.)
    """
    src = csv_dir / "file_key.csv"
    if not src.exists():
        return None
    df = pd.read_csv(src, dtype=str, keep_default_na=False, na_values=[""])
    df = df.dropna(how="all")
    if df.empty:
        return None
    dest_dir = cfg.ARCHIVE / "file_keys"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{submission_stem}_file_key.csv"
    df["_submission"] = submission_stem
    df.to_csv(dest, index=False)
    return dest


def archive_workbook(xlsx: Path) -> Path:
    """Move a successful workbook into processed/YYYY/MM/."""
    now = datetime.now()
    dest_dir = cfg.PROCESSED / f"{now.year:04d}" / f"{now.month:02d}"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / xlsx.name
    if dest.exists():
        stamp = now.strftime("%Y%m%d_%H%M%S")
        dest = dest_dir / f"{xlsx.stem}_{stamp}{xlsx.suffix}"
    xlsx.replace(dest)
    return dest


def append_ingest_log(entry: str) -> None:
    cfg.LOGS.mkdir(parents=True, exist_ok=True)
    log_path = cfg.LOGS / f"ingest_{datetime.now().strftime('%Y-%m-%d')}.log"
    with log_path.open("a", encoding="utf-8") as f:
        f.write(entry.rstrip() + "\n")


# =============================================================================
# Per-submission driver
# =============================================================================

def ingest_one(csv_dir: Path, xlsx: Path | None) -> int:
    """
    Ingest one submission. Returns exit-code-shaped int.
        0 = success, 1 = validation/DB error rolled back, 2 = preflight error
    """
    stem = csv_dir.name
    print(f"=== {stem} ===")

    if not csv_dir.exists() or not csv_dir.is_dir():
        print(f"  ERROR: CSV dir not found: {csv_dir}", file=sys.stderr)
        return 2

    info = read_submission_info(csv_dir)
    submitter = info.get("submitter_name", "?")
    email = info.get("submitter_email", "?")
    print(f"  Submitter: {submitter} <{email}>")

    conn = sqlite3.connect(cfg.DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        with conn:  # implicit transaction; commits on clean exit, rolls back on raise
            for sheet in INGEST_ORDER:
                df = load_csv(csv_dir, sheet)
                df = prepare_frame(sheet, df)
                inserted, seen = insert_frame(conn, sheet, df)
                if seen:
                    skipped = seen - inserted
                    tail = f" ({skipped} already present)" if skipped else ""
                    print(f"  {sheet:22s} {inserted:>3}/{seen:<3} rows inserted{tail}")
    except sqlite3.IntegrityError as e:
        print(f"  FAILED - integrity error: {e}", file=sys.stderr)
        print(f"  Transaction rolled back. No changes committed for this submission.", file=sys.stderr)
        append_ingest_log(f"{datetime.now().isoformat(timespec='seconds')}  FAILED  {stem}  {submitter}  IntegrityError: {e}")
        return 1
    except Exception as e:
        print(f"  FAILED - {type(e).__name__}: {e}", file=sys.stderr)
        print(f"  Transaction rolled back. No changes committed for this submission.", file=sys.stderr)
        append_ingest_log(f"{datetime.now().isoformat(timespec='seconds')}  FAILED  {stem}  {submitter}  {type(e).__name__}: {e}")
        return 1
    finally:
        conn.close()

    # Persist file_key rows for the instrument-file ingester (post-commit).
    fk_dest = dump_file_keys(csv_dir, stem)
    if fk_dest:
        print(f"  file_key -> {fk_dest.relative_to(cfg.PROJECT_ROOT)}")

    # Archive the source workbook so inbox reflects only unfinished business.
    if xlsx and xlsx.exists():
        dest = archive_workbook(xlsx)
        print(f"  Archived: {dest.relative_to(cfg.PROJECT_ROOT)}")
    else:
        print(f"  (no source workbook found in inbox to archive)")

    append_ingest_log(f"{datetime.now().isoformat(timespec='seconds')}  OK      {stem}  {submitter}")
    return 0


# =============================================================================
# Batch driver
# =============================================================================

def find_pending() -> list:
    """Every csv_export subdir whose corresponding xlsx is still in inbox."""
    pending = []
    if not cfg.CSV_EXPORT.exists():
        return pending
    for csv_dir in sorted(cfg.CSV_EXPORT.iterdir()):
        if not csv_dir.is_dir():
            continue
        xlsx = cfg.INBOX / f"{csv_dir.name}.xlsx"
        if xlsx.exists():
            pending.append((csv_dir, xlsx))
    return pending


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Ingest validated submission CSVs into radon.db."
    )
    parser.add_argument(
        "csv_dir", nargs="?", type=Path,
        help="Optional path to one csv_export subdir. If omitted, scans "
             "archive/csv_export/ for submissions whose xlsx is still in inbox.",
    )
    args = parser.parse_args()

    if not cfg.DB_PATH.exists():
        print(f"ERROR: database not found: {cfg.DB_PATH}", file=sys.stderr)
        print(f"Create it first with create_db.py.", file=sys.stderr)
        return 2

    # Assemble work list -------------------------------------------------------
    if args.csv_dir:
        stem = args.csv_dir.name
        xlsx = cfg.INBOX / f"{stem}.xlsx"
        work = [(args.csv_dir, xlsx if xlsx.exists() else None)]
    else:
        work = find_pending()
        if not work:
            print(f"No pending submissions in {cfg.CSV_EXPORT}")
            return 0
        print(f"Found {len(work)} pending submission{'s' if len(work) != 1 else ''}.\n")

    # One backup for the whole batch. If anything fails mid-batch, per-submission
    # transactions still roll back individually; the batch backup is the wider
    # safety net for undoing everything at once.
    backup = backup_db(cfg.DB_PATH)
    print(f"DB backup: {backup.relative_to(cfg.PROJECT_ROOT)}\n")

    passed = failed = errored = 0
    for csv_dir, xlsx in work:
        code = ingest_one(csv_dir, xlsx)
        if code == 0:
            passed += 1
        elif code == 1:
            failed += 1
        else:
            errored += 1
        print()

    print(f"Summary: {passed} ingested, {failed} failed, {errored} errored")
    print(f"Full DB backup available at {backup.relative_to(cfg.PROJECT_ROOT)}")
    return 0 if (failed == 0 and errored == 0) else 1


if __name__ == "__main__":
    sys.exit(main())