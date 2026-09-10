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

-- Radon: analyses and cycles
--
-- Uncertainty convention: all radon/thoron uncertainty columns are 2-SIGMA,
-- matching the Durridge Capture export ("... 2-Sigma Uncert. [pCi/L]").
--
-- Concentration convention: for a water protocol (WAT40/WAT250) the RAD8 reports
-- the SAMPLE WATER concentration directly -- internally it multiplies the air-loop
-- reading by a fixed, sample-volume-dependent conversion coefficient (2.82 for
-- 250 mL, 17.9 for 40 mL). So avg_rn_pcil / rn_pcil already hold radon-in-water
-- (= Capture "Radon Concentration", which matches "Radon In Water Concentration"
-- to rounding; both are the water phase). corrected_rn_pcil is reserved for
-- post-hoc corrections such as decay-correction back to collection time (see
-- holding_time_hours); NULL at ingest.
-- ============================================================
CREATE TABLE radon_analyses (
    analysis_id             TEXT NOT NULL PRIMARY KEY,                    -- 'BCR_01_20261202_radon_T1'
    sample_id               TEXT NOT NULL REFERENCES samples(sample_id),
    inst_id                 TEXT NOT NULL REFERENCES instruments(inst_id),
    test_number             INTEGER DEFAULT 1,
    protocol                TEXT, --'WAT40, WAT250'
    datetime_analysis_utc   TEXT NOT NULL,
    holding_time_hours      REAL,
    avg_rn_pcil             REAL,                           -- mean radon-in-water over used cycles (RAD8 already applied the sample-volume coefficient)
    avg_rn_unc_2s           REAL,                           -- 2-sigma; propagated from per-cycle 2-sigma counting uncertainties
    corrected_rn_pcil       REAL,                           -- decay-corrected-to-collection radon-in-water (downstream; uses holding_time_hours); NULL at ingest
    avg_thoron_pcil         REAL,
    avg_thoron_unc_2s       REAL,                           -- 2-sigma
    n_cycles                INTEGER DEFAULT 4,
    n_cycles_flagged        INTEGER,
    personnel               TEXT,
    source_file             TEXT NOT NULL,
    source_sha256           TEXT NOT NULL UNIQUE,
    config_json             TEXT,                           -- dataConfigurationSetters + embeddedRAD8Profile + setup blob
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
    cycle_utc       TEXT NOT NULL,      -- cycle START (from .rd8 Date_Time epoch, UTC). NB: Capture CSV labels rows by the cycle END = start + cycle_length_s
    cycle_length_s  INTEGER,
    humidity_pct    REAL,               -- relative humidity (%)
    temp_d_c        REAL,               -- air temperature, digital probe (deg C)
    temp_a_c        REAL,               -- air temperature, analog probe (deg C)
    barometer_mb    REAL,
    baro_temp_c     REAL,               -- barometer temperature (deg C)
    hv_counts       INTEGER,            -- high-voltage counts (instrument health / QC)
    pump_current_ma REAL,               -- pump current (mA) (instrument health / QC)
    total_counts    TEXT,               -- hex string like '0x0000000000000105' (equals decimal 'Total Counts')
    cpm             REAL,
    mode            TEXT,               -- 'R' (Rapid), 'N' (Normal), 'S' (Sniff)
    rn_pcil         REAL,               -- radon-in-water reported by RAD8 (air-loop x sample-volume coefficient) = Capture 'Radon Concentration'
    rn_unc_2s       REAL,               -- 2-sigma
    thoron_pcil     REAL,
    thoron_unc_2s   REAL,               -- 2-sigma
    spectrum_b64    TEXT,
    qc_flag         TEXT DEFAULT 'ok',
    qc_notes        TEXT,
    UNIQUE (analysis_id, cycle_no)
);
CREATE INDEX idx_radon_cycles_analysis ON radon_cycles(analysis_id);
CREATE INDEX idx_radon_cycles_utc ON radon_cycles(cycle_utc);
 