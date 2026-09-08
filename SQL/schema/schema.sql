-- texashydro-db schema v0.1
-- Author: Will Eagle
-- dates YYYYMMDD

PRAGMA foreign_keys = ON;

-- ============================================================
-- Sites: physical sampling locations
-- ============================================================
CREATE TABLE sites (
    site_id         TEXT Not NULL PRIMARY KEY,           -- use SWN for wells and springs, and Marcus' site codes for streams (eg DEV100) with numbers arbitrarily increasing downstream
    site_type       TEXT NOT NULL CHECK (site_type IN ('well', 'spring', 'stream', 'lake', 'other')),
    latitude        REAL,
    longitude       REAL,
    elevation_m     REAL,
    coord_crs       TEXT DEFAULT 'EPSG:4326',
    aquifer         TEXT,
    county          TEXT,
    notes           TEXT,
    created_utc     TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Alternate names/IDs for sites (agency numbers, historical names, etc.)
CREATE TABLE site_names (
    name_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    site_id     TEXT NOT NULL REFERENCES sites(site_id),
    name        TEXT NOT NULL,
    name_type   TEXT,                  -- 'usgs_nwis', 'twdb_swn', 'local', etc.
    source      TEXT,
    notes       TEXT,
    UNIQUE (site_id, name, name_type)
);
CREATE INDEX idx_site_names_name ON site_names(name);

-- ============================================================
-- Instruments
-- ============================================================
CREATE TABLE instruments (
    inst_id     TEXT NOT NULL PRIMARY KEY,               -- 'RAD8-01', 'FT2-EAA1'
    model       TEXT NOT NULL,                  -- 'RAD8', 'FlowTracker2'
    serial      TEXT,
    notes       TEXT
);

-- ============================================================
-- Projects and campaigns
-- ============================================================
CREATE TABLE projects (
    project_id      TEXT NOT NULL PRIMARY KEY,           -- 'DR_TWDB_202X'
    name            TEXT NOT NULL,
    pi              TEXT,
    funding_source  TEXT,
    start_date      DATE NOT NULL,
    end_date        DATE,
    description     TEXT
);

CREATE TABLE campaigns (
    campaign_id     TEXT NOT NULL PRIMARY KEY,           -- 'DR_KarstHydro_2026'
    project_id      TEXT REFERENCES projects(project_id),
    name            TEXT NOT NULL,
    description     TEXT,
    lead            TEXT,
    start_date      DATE NOT NULL,
    end_date        DATE,
    notes           TEXT,
    created_utc     TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ============================================================
-- Site visits
-- ============================================================
CREATE TABLE visits (
    visit_id            TEXT NOT NULL PRIMARY KEY, --'DEV100_20261202'
    site_id             TEXT NOT NULL REFERENCES sites(site_id),
    campaign_id         TEXT REFERENCES campaigns(campaign_id),
    datetime_start_utc  TEXT NOT NULL,
    datetime_end_utc    TEXT,
    personnel           TEXT,
    weather             TEXT,
    flow_condition      TEXT,                   -- 'baseflow', 'stormflow', 'high_stage', 'dry'
    fieldnotes_filename TEXT,                   -- path to scanned notebook PDF
    notes               TEXT,
    created_utc         TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ============================================================
-- Samples (physical containers collected)
-- ============================================================
CREATE TABLE samples (
    sample_id               TEXT NOT NULL PRIMARY KEY, --DEV100_20261202_radon OR _cations OR _TWDBsample, etc
    visit_id                TEXT NOT NULL REFERENCES visits(visit_id),
    datetime_collection_utc TEXT NOT NULL,
    sample_type             TEXT NOT NULL CHECK (sample_type IN ('grab', 'field_rep', 'aliquot', 'blank', 'other')),
    intended_analysis       TEXT,                   -- 'radon', 'cations', 'raw', 'd18O'
    replicate               TEXT DEFAULT 'A',
    container               TEXT, -- 'RAD8_01_bottle1'
    filtered                INTEGER DEFAULT 0,      -- boolean
    parent_sample_id        TEXT REFERENCES samples(sample_id),
    notes                   TEXT
);
CREATE INDEX idx_samples_visit ON samples(visit_id);
CREATE INDEX idx_samples_datetime ON samples(datetime_collection_utc);

-- ============================================================
-- Field measurements (scalar values from probes, tapes, gauges)
-- ============================================================
CREATE TABLE field_measurements (
    meas_id         TEXT NOT NULL PRIMARY KEY, --'DEV100_20261202_water_temp'
    visit_id        TEXT NOT NULL REFERENCES visits(visit_id),
    parameter       TEXT NOT NULL,                          -- 'water_temp', 'pH', 'EC', 'DTW', 'stage', 'discharge'
    value           REAL NOT NULL,
    unit            TEXT NOT NULL,
    uncertainty     REAL,
    datetime_utc    TEXT NOT NULL,
    method          TEXT,                                   -- 'YSI ProDSS', 'steel tape'
    inst_id         TEXT REFERENCES instruments(inst_id),
    notes           TEXT
);
CREATE INDEX idx_fm_visit ON field_measurements(visit_id);
CREATE INDEX idx_fm_parameter ON field_measurements(parameter);

-- ============================================================
-- Radon: analyses and cycles
-- ============================================================
CREATE TABLE radon_analyses (
    analysis_id             TEXT NOT NULL PRIMARY KEY,                    -- 'BCR_01_20261202_radon_T1'
    sample_id               TEXT NOT NULL REFERENCES samples(sample_id),
    inst_id                 TEXT NOT NULL REFERENCES instruments(inst_id),
    test_number             INTEGER DEFAULT 1,
    protocol                TEXT, --'WAT40, WAT250'
    datetime_analysis_utc   TEXT NOT NULL,
    holding_time_days       REAL,
    avg_rn_pcil             REAL,
    avg_rn_unc              REAL,
    corrected_rn_pcil       REAL,
    avg_thoron_pcil         REAL,
    avg_thoron_unc          REAL,
    n_cycles                INTEGER DEFAULT 4,
    n_cycles_flagged        INTEGER,
    personnel               TEXT,
    source_file             TEXT NOT NULL,
    source_sha256           TEXT NOT NULL UNIQUE,
    config_json             TEXT,                           -- dataConfigurationSetters blob
    qc_flag                 TEXT DEFAULT 'ok',
    notes                   TEXT,
    ingested_utc            TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_radon_analyses_sample ON radon_analyses(sample_id);
CREATE INDEX idx_radon_analyses_datetime ON radon_analyses(datetime_analysis_utc);

CREATE TABLE radon_cycles (
    cycle_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    analysis_id     TEXT NOT NULL REFERENCES radon_analyses(analysis_id),
    cycle_no        INTEGER NOT NULL,
    cycle_utc       TEXT NOT NULL,
    cycle_length_s  INTEGER,
    humidity_pct    REAL,
    temp_d_c        REAL,
    temp_a_c        REAL,
    barometer_mb    REAL,
    total_counts    TEXT,               -- hex string like '0x0000000000000105'
    cpm             REAL,
    mode            TEXT,               -- 'R', 'N', 'S'
    rn_pcil         REAL,
    rn_unc          REAL,
    thoron_pcil     REAL,
    thoron_unc      REAL,
    spectrum_b64    TEXT,
    qc_flag         TEXT DEFAULT 'ok',
    qc_notes        TEXT,
    UNIQUE (analysis_id, cycle_no)
);
CREATE INDEX idx_radon_cycles_analysis ON radon_cycles(analysis_id);
CREATE INDEX idx_radon_cycles_utc ON radon_cycles(cycle_utc);