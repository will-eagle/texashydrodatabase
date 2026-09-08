"""
Validate a texashydro-db submission workbook.

Two modes:

    # Single-file mode (dev / testing):
    python validate.py path/to/submission.xlsx

    # Inbox mode (deployment default):
    python validate.py
        -> scans <PROJECT_ROOT>/submissions/inbox/*.xlsx
        -> exports clean CSVs to <PROJECT_ROOT>/archive/csv_export/<stem>/
        -> moves failed workbooks to <PROJECT_ROOT>/submissions/rejected/<stem>/
           along with an errors.txt describing why

Exit codes:
    0 = all workbooks passed (or --no-export)
    1 = at least one failed validation
    2 = file / IO error before validation could run
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

import config as cfg

import warnings

warnings.filterwarnings("ignore", category=UserWarning, module="openpyxl")

# ---- Allowed vocabularies (mirror schema.sql CHECK constraints) --------------
ALLOWED_SITE_TYPES = {"well", "spring", "stream", "lake", "other"}
ALLOWED_SAMPLE_TYPES = {"grab", "field_rep", "aliquot", "blank", "other"}
ALLOWED_FLOW_CONDITIONS = {"baseflow", "stormflow", "high_stage", "dry"}
ALLOWED_FILE_TYPES = {"rd8", "ft", "fieldnotes_pdf", "other"}
ALLOWED_PARAMETERS = {
    "water_temp", "pH", "EC", "DO", "ORP", "turbidity",
    "DTW", "stage", "discharge", "air_temp", "barometer", "other",
}

# ---- ID / datetime format patterns ------------------------------------------
RE_SWN = re.compile(r"^\d{7}$")                # TWDB State Well Number
RE_SITECODE = re.compile(r"^[A-Z]{3}\d{3}$")   # Marcus stream codes (e.g. DEV100)
RE_DATETIME = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")  # YYYY-MM-DD HH:MM

REQUIRED_SUBMISSION_FIELDS = {"submitter_name", "submitter_email", "submission_date"}
LOOKUP_SHEETS = {"README", "valid_sites", "counties", "aquifer_codes"}


# =============================================================================
# Helpers
# =============================================================================

def cell(row, col) -> str:
    """Return trimmed string from a pandas row; empty string for NaN/missing."""
    v = row.get(col)
    if pd.isna(v):
        return ""
    return str(v).strip()


def is_valid_site_id(sid: str) -> bool:
    return bool(RE_SWN.match(sid) or RE_SITECODE.match(sid))


def is_valid_datetime(s: str) -> bool:
    return bool(RE_DATETIME.match(s))


def row_is_blank(row, cols) -> bool:
    return not any(cell(row, c) for c in cols)


def load_known_ids(xls: pd.ExcelFile) -> dict:
    """
    Load IDs already known to the system. Currently only pulls the sites list
    from the workbook's valid_sites lookup sheet.

    TODO: replace with live queries against radon.db so already-ingested
    visit_ids and sample_ids are also resolvable across submissions.
    """
    known = {"sites": set(), "visits": set(), "samples": set()}
    if "valid_sites" in xls.sheet_names:
        df = pd.read_excel(xls, sheet_name="valid_sites")
        if "site_id" in df.columns:
            known["sites"] = {
                str(s).strip() for s in df["site_id"].dropna() if str(s).strip()
            }
    return known


# =============================================================================
# Per-sheet validation
# =============================================================================

def check_submission_info(xls, errors, warnings):
    if "submission_info" not in xls.sheet_names:
        errors.append("submission_info sheet is missing.")
        return
    df = pd.read_excel(xls, sheet_name="submission_info")
    if not {"field", "value"}.issubset(df.columns):
        errors.append("[submission_info] missing expected 'field'/'value' columns.")
        return
    filled = {}
    for _, r in df.iterrows():
        k = cell(r, "field")
        v = cell(r, "value")
        if k:
            filled[k] = v
    for req in REQUIRED_SUBMISSION_FIELDS:
        if not filled.get(req):
            errors.append(f"[submission_info] '{req}' is required.")


def check_sites(xls, errors, warnings) -> set:
    """Returns the set of site_ids introduced in this submission."""
    submitted = set()
    if "sites" not in xls.sheet_names:
        return submitted
    df = pd.read_excel(xls, sheet_name="sites")
    cols = df.columns.tolist()
    for i, row in df.iterrows():
        row_num = i + 2
        if row_is_blank(row, cols):
            continue
        sid = cell(row, "site_id")
        if not sid:
            errors.append(f"[sites] Row {row_num}: 'site_id' is required.")
            continue
        if not is_valid_site_id(sid):
            warnings.append(
                f"[sites] Row {row_num}: non-standard site_id '{sid}' "
                f"(expected 7-digit SWN or 3-letter+3-digit code like DEV100)."
            )
        if sid in submitted:
            errors.append(
                f"[sites] Row {row_num}: duplicate site_id '{sid}' within this submission."
            )
        submitted.add(sid)

        stype = cell(row, "site_type").lower()
        if stype and stype not in ALLOWED_SITE_TYPES:
            errors.append(
                f"[sites] Row {row_num}: invalid site_type '{stype}'. "
                f"Must be one of {sorted(ALLOWED_SITE_TYPES)}."
            )

        lat = row.get("latitude")
        if pd.isna(lat) or str(lat).strip() == "":
            errors.append(f"[sites] Row {row_num}: 'latitude' is required.")
        if pd.notna(lat):
            try:
                if not -90.0 <= float(lat) <= 90.0:
                    errors.append(
                        f"[sites] Row {row_num}: latitude {lat} out of range (-90..90)."
                    )
            except (TypeError, ValueError):
                errors.append(f"[sites] Row {row_num}: latitude '{lat}' is not numeric.")

        lon = row.get("longitude")
        if pd.isna(lon) or str(lon).strip() == "":
            errors.append(f"[sites] Row {row_num}: 'longitude' is required.")
        if pd.notna(lon):
            try:
                if not -180.0 <= float(lon) <= 180.0:
                    errors.append(
                        f"[sites] Row {row_num}: longitude {lon} out of range (-180..180)."
                    )
            except (TypeError, ValueError):
                errors.append(f"[sites] Row {row_num}: longitude '{lon}' is not numeric.")
    return submitted


def check_visits(xls, errors, warnings, known_sites) -> set:
    submitted = set()
    if "visits" not in xls.sheet_names:
        return submitted
    df = pd.read_excel(xls, sheet_name="visits")
    cols = df.columns.tolist()
    for i, row in df.iterrows():
        row_num = i + 2
        if row_is_blank(row, cols):
            continue
        vid = cell(row, "visit_id")
        sid = cell(row, "site_id")
        dt = cell(row, "datetime_start")

        if not sid:
            errors.append(f"[visits] Row {row_num}: 'site_id' is required.")
        elif sid not in known_sites:
            errors.append(
                f"[visits] Row {row_num}: site_id '{sid}' not defined in sites tab or database."
            )

        if not dt:
            errors.append(f"[visits] Row {row_num}: 'datetime_start' is required.")
        elif not is_valid_datetime(dt):
            errors.append(
                f"[visits] Row {row_num}: datetime_start '{dt}' not in YYYY-MM-DD HH:MM format."
            )

        dt_end = cell(row, "datetime_end")
        if dt_end and not is_valid_datetime(dt_end):
            errors.append(
                f"[visits] Row {row_num}: datetime_end '{dt_end}' not in YYYY-MM-DD HH:MM format."
            )

        fc = cell(row, "flow_condition").lower()
        if fc and fc not in ALLOWED_FLOW_CONDITIONS:
            errors.append(
                f"[visits] Row {row_num}: flow_condition '{fc}' invalid. "
                f"Must be one of {sorted(ALLOWED_FLOW_CONDITIONS)}."
            )

        if vid:
            if vid in submitted:
                errors.append(
                    f"[visits] Row {row_num}: duplicate visit_id '{vid}' within submission."
                )
            submitted.add(vid)
    return submitted


def check_samples(xls, errors, warnings, known_visits) -> set:
    submitted = set()
    if "samples" not in xls.sheet_names:
        return submitted
    df = pd.read_excel(xls, sheet_name="samples")
    cols = df.columns.tolist()
    for i, row in df.iterrows():
        row_num = i + 2
        if row_is_blank(row, cols):
            continue
        samp_id = cell(row, "sample_id")
        vid = cell(row, "visit_id")
        dt = cell(row, "datetime_collected")

        if not vid:
            errors.append(f"[samples] Row {row_num}: 'visit_id' is required.")
        elif vid not in known_visits:
            errors.append(
                f"[samples] Row {row_num}: visit_id '{vid}' not defined in visits tab or database."
            )

        if not dt:
            errors.append(f"[samples] Row {row_num}: 'datetime_collected' is required.")
        elif not is_valid_datetime(dt):
            errors.append(
                f"[samples] Row {row_num}: datetime_collected '{dt}' not in YYYY-MM-DD HH:MM format."
            )

        stype = cell(row, "sample_type").lower()
        if stype and stype not in ALLOWED_SAMPLE_TYPES:
            errors.append(
                f"[samples] Row {row_num}: sample_type '{stype}' invalid. "
                f"Must be one of {sorted(ALLOWED_SAMPLE_TYPES)}."
            )

        filt = cell(row, "filtered")
        if filt and filt not in {"0", "1"}:
            errors.append(f"[samples] Row {row_num}: filtered '{filt}' must be 0 or 1.")

        if samp_id:
            if samp_id in submitted:
                errors.append(
                    f"[samples] Row {row_num}: duplicate sample_id '{samp_id}' within submission."
                )
            submitted.add(samp_id)
    return submitted


def check_field_measurements(xls, errors, warnings, known_visits):
    if "field_measurements" not in xls.sheet_names:
        return
    df = pd.read_excel(xls, sheet_name="field_measurements")
    cols = df.columns.tolist()
    seen = set()
    for i, row in df.iterrows():
        row_num = i + 2
        if row_is_blank(row, cols):
            continue
        mid = cell(row, "meas_id")
        vid = cell(row, "visit_id")
        param = cell(row, "parameter")
        val = row.get("value")
        unit = cell(row, "unit")
        dt = cell(row, "datetime_measured")

        if not vid:
            errors.append(f"[field_measurements] Row {row_num}: 'visit_id' is required.")
        elif vid not in known_visits:
            errors.append(
                f"[field_measurements] Row {row_num}: visit_id '{vid}' "
                f"not defined in visits tab or database."
            )

        if param and param not in ALLOWED_PARAMETERS:
            errors.append(
                f"[field_measurements] Row {row_num}: parameter '{param}' invalid. "
                f"Must be one of {sorted(ALLOWED_PARAMETERS)}."
            )

        if pd.isna(val) or str(val).strip() == "":
            errors.append(f"[field_measurements] Row {row_num}: 'value' is required.")
        else:
            try:
                float(val)
            except (TypeError, ValueError):
                errors.append(
                    f"[field_measurements] Row {row_num}: value '{val}' is not numeric."
                )

        if not unit:
            errors.append(f"[field_measurements] Row {row_num}: 'unit' is required.")

        if dt and not is_valid_datetime(dt):
            errors.append(
                f"[field_measurements] Row {row_num}: datetime_measured '{dt}' "
                f"not in YYYY-MM-DD HH:MM format."
            )

        if mid:
            if mid in seen:
                errors.append(
                    f"[field_measurements] Row {row_num}: duplicate meas_id '{mid}' "
                    f"within submission (append _2, _3, ... per README)."
                )
            seen.add(mid)


def check_file_key(xls, errors, warnings, known_samples):
    if "file_key" not in xls.sheet_names:
        return
    df = pd.read_excel(xls, sheet_name="file_key")
    cols = df.columns.tolist()
    seen = set()
    for i, row in df.iterrows():
        row_num = i + 2
        if row_is_blank(row, cols):
            continue
        fname = cell(row, "filename")
        ftype = cell(row, "file_type").lower()
        samp = cell(row, "sample_id")

        if not fname:
            errors.append(f"[file_key] Row {row_num}: 'filename' is required.")
        else:
            if fname in seen:
                errors.append(
                    f"[file_key] Row {row_num}: duplicate filename '{fname}' within submission."
                )
            seen.add(fname)

        if ftype and ftype not in ALLOWED_FILE_TYPES:
            errors.append(
                f"[file_key] Row {row_num}: file_type '{ftype}' invalid. "
                f"Must be one of {sorted(ALLOWED_FILE_TYPES)}."
            )

        # NOTE: current template forces every file to link via sample_id.
        # If .ft (FlowTracker) files need to link at visit level instead,
        # extend the template with a visit_id column and add a check here.
        if not samp:
            errors.append(f"[file_key] Row {row_num}: 'sample_id' is required.")
        elif samp not in known_samples:
            errors.append(
                f"[file_key] Row {row_num}: sample_id '{samp}' "
                f"not defined in samples tab or database."
            )


# =============================================================================
# Orchestration
# =============================================================================

def validate(xls: pd.ExcelFile) -> tuple[list, list]:
    errors, warnings = [], []
    known = load_known_ids(xls)

    check_submission_info(xls, errors, warnings)
    submitted_sites = check_sites(xls, errors, warnings)
    known_sites = known["sites"] | submitted_sites

    submitted_visits = check_visits(xls, errors, warnings, known_sites)
    known_visits = known["visits"] | submitted_visits

    submitted_samples = check_samples(xls, errors, warnings, known_visits)
    known_samples = known["samples"] | submitted_samples

    check_field_measurements(xls, errors, warnings, known_visits)
    check_file_key(xls, errors, warnings, known_samples)

    return errors, warnings


def export_csvs(xls: pd.ExcelFile, output_dir: Path) -> list:
    output_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for sheet in xls.sheet_names:
        if sheet in LOOKUP_SHEETS:
            continue
        df = pd.read_excel(xls, sheet_name=sheet)
        out = output_dir / f"{sheet}.csv"
        df.to_csv(out, index=False)
        written.append(out)
    return written


# =============================================================================
# Rejection handling
# =============================================================================

def is_in_inbox(p: Path) -> bool:
    """True if p lives under the submissions/inbox directory."""
    try:
        p.resolve().relative_to(cfg.INBOX.resolve())
        return True
    except (ValueError, FileNotFoundError):
        return False


def move_to_rejected(xlsx: Path, errors: list, warnings: list) -> Path:
    """Move a failed workbook to submissions/rejected/<stem>/ and drop an
    errors.txt beside it. Returns the new xlsx path."""
    stem = xlsx.stem
    dest_dir = cfg.REJECTED / stem
    dest_dir.mkdir(parents=True, exist_ok=True)

    dest_xlsx = dest_dir / xlsx.name
    if dest_xlsx.exists():
        # This submission was rejected before — keep the old copy, append a stamp.
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest_xlsx = dest_dir / f"{stem}_{stamp}{xlsx.suffix}"

    xlsx.replace(dest_xlsx)

    err_file = dest_dir / f"{dest_xlsx.stem}_errors.txt"
    with err_file.open("w", encoding="utf-8") as f:
        f.write(f"Validation failed for {xlsx.name}\n")
        f.write(f"Rejected: {datetime.now().isoformat(timespec='seconds')}\n\n")
        f.write(f"ERRORS ({len(errors)}):\n")
        for e in errors:
            f.write(f"  - {e}\n")
        if warnings:
            f.write(f"\nWARNINGS ({len(warnings)}):\n")
            for w in warnings:
                f.write(f"  - {w}\n")

    return dest_xlsx


# =============================================================================
# Per-workbook driver
# =============================================================================

def process_one(
    xlsx_path: Path,
    csv_out_root: Path,
    no_export: bool,
    no_move: bool,
) -> int:
    """
    Validate one workbook, print results, and optionally act on them.
    Returns an exit-code-shaped int (0 = pass, 1 = fail, 2 = IO error).
    """
    if not xlsx_path.exists():
        print(f"ERROR: file not found: {xlsx_path}", file=sys.stderr)
        return 2
    try:
        xls = pd.ExcelFile(xlsx_path)
    except Exception as e:
        print(f"ERROR: could not open workbook {xlsx_path.name}: {e}", file=sys.stderr)
        return 2

    errors, warnings = validate(xls)

    if warnings:
        print(f"  {len(warnings)} warning(s):")
        for w in warnings:
            print(f"    - {w}")

    if errors:
        print(f"  FAILED - {len(errors)} error(s):")
        for e in errors:
            print(f"    - {e}")
        if not no_move and is_in_inbox(xlsx_path):
            xls.close()  # release the file handle before renaming
            moved = move_to_rejected(xlsx_path, errors, warnings)
            print(f"  Moved to {moved.relative_to(cfg.PROJECT_ROOT)}")
        return 1

    print("  Validation passed.")
    if not no_export:
        out_dir = csv_out_root / xlsx_path.stem
        written = export_csvs(xls, out_dir)
        print(f"  Exported {len(written)} sheet(s) to {out_dir.relative_to(cfg.PROJECT_ROOT)}")
    return 0


# =============================================================================
# Main
# =============================================================================

def find_inbox_workbooks() -> list:
    """Return sorted .xlsx paths in the inbox, skipping Excel lock files."""
    if not cfg.INBOX.exists():
        return []
    return sorted(
        p for p in cfg.INBOX.glob("*.xlsx")
        if not p.name.startswith("~$")
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate texashydro-db submission workbook(s) and export sheets to CSV."
    )
    parser.add_argument(
        "file", nargs="?", type=Path,
        help="Optional path to a single .xlsx. If omitted, scans submissions/inbox/.",
    )
    parser.add_argument(
        "-o", "--output", type=Path, default=cfg.CSV_EXPORT,
        help=f"CSV output root; per-submission subdirs land inside. "
             f"Default: {cfg.CSV_EXPORT}",
    )
    parser.add_argument(
        "--no-export", action="store_true",
        help="Validate only; skip CSV export on success.",
    )
    parser.add_argument(
        "--no-move", action="store_true",
        help="Don't move rejected workbooks to submissions/rejected/.",
    )
    args = parser.parse_args()

    # Single-file mode ---------------------------------------------------------
    if args.file:
        print(f"=== {args.file.name} ===")
        return process_one(args.file, args.output, args.no_export, args.no_move)

    # Inbox mode ---------------------------------------------------------------
    if not cfg.INBOX.exists():
        print(f"ERROR: inbox not found: {cfg.INBOX}", file=sys.stderr)
        return 2

    xlsxes = find_inbox_workbooks()
    if not xlsxes:
        print(f"No .xlsx files in {cfg.INBOX}")
        return 0

    print(f"Scanning {cfg.INBOX} ({len(xlsxes)} workbook{'s' if len(xlsxes) != 1 else ''})\n")
    passed = failed = errored = 0
    for xlsx in xlsxes:
        print(f"=== {xlsx.name} ===")
        code = process_one(xlsx, args.output, args.no_export, args.no_move)
        if code == 0:
            passed += 1
        elif code == 1:
            failed += 1
        else:
            errored += 1
        print()

    print(f"Summary: {passed} passed, {failed} failed, {errored} errored")
    return 0 if (failed == 0 and errored == 0) else 1


if __name__ == "__main__":
    sys.exit(main())