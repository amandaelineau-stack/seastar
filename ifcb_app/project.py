"""IFCB project folders: naming, creation and scanning (no UI code here)."""
import csv
import io
import json
import os
import re

# Layout of Y:\ifcb_snXXX_IDcruise_namecruise, with the WHOI v1.3.1 features folder of the recent cruises
FEATURES_DIR = "whoi_v4_V131_features"
STANDARD_DIRS = ["raw", "metadata", "log", FEATURES_DIR, "ecotaxa_import_zip_seastar", "figures"]

DAY_RE = re.compile(r"D\d{8}")
BIN_RE = re.compile(r"D\d{8}T\d{6}_IFCB\d+")


def whoi_version():
    """Installed WHOI ifcb-features, e.g. "v1.3.1 (commit 1d0687e)"; None if not installed.
    The git tag pip recorded is used first: tag v1.3.1 still declares version 1.3.0 in its pyproject.toml."""
    from importlib import metadata
    try:
        dist = metadata.distribution("ifcb-features")
    except metadata.PackageNotFoundError:
        return None
    vcs = json.loads(dist.read_text("direct_url.json") or "{}").get("vcs_info", {})
    if vcs.get("requested_revision"):
        return f"{vcs['requested_revision']} (commit {vcs.get('commit_id', '')[:7]})"
    return dist.version


def latest_whoi_release():
    """(tag, date) of the latest WHOI ifcb-features release on GitHub, e.g. ("v1.3.1", "2026-08-12");
    None when GitHub cannot be reached (offline, proxy, rate limit)."""
    from urllib.request import urlopen
    try:
        with urlopen("https://api.github.com/repos/WHOIGit/ifcb-features/releases/latest", timeout=5) as r:
            release = json.load(r)
        return release["tag_name"], release["published_at"][:10]
    except Exception:
        return None


VERSIONS_CSV = "feature_extractor_versions.csv"


def record_extractor_version(features_dir, bin_name, version):
    """One row per extracted bin. SeaSTAR joins it on `bin`: feature_extractor -> process_feature_extractor,
    and with --bindcol "feature_extractor_release:t:process_feature_extractor_release". A re-extracted bin
    gets a new row; SeaSTAR keeps the last matching row."""
    path = os.path.join(features_dir, VERSIONS_CSV)
    new = not os.path.isfile(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["bin", "feature_extractor", "feature_extractor_release"])
        w.writerow([bin_name, extractor_name(version), version])


def extractor_name(version):
    """Same value as the AR7E tables: "v1.3.1 (commit …)" -> "whoi_v4_release131"."""
    m = re.match(r"v?(\d+)\.(\d+)\.(\d+)", version or "")
    return "whoi_v4_release" + "".join(m.groups()) if m else "whoi_v4"


def _slug(text):
    return re.sub(r"[^a-z0-9]+", "", text.strip().lower())


def project_name(serial, cruise_id, cruise_name):
    """ifcb_sn<serial>_<cruiseid>_<cruisename>, e.g. ifcb_sn225_ce26008_ar7e."""
    parts = [_slug(serial), _slug(cruise_id), _slug(cruise_name)]
    if not all(parts):
        raise ValueError("Serial number, cruise ID and cruise name are all required")
    return "ifcb_sn{}_{}_{}".format(*parts)


def create_project(parent_dir, name):
    """Create the project and its standard sub-folders. Never touches existing content."""
    if not os.path.isdir(parent_dir):
        raise FileNotFoundError("Parent folder does not exist: " + parent_dir)
    root = os.path.join(parent_dir, name)
    if os.path.exists(root):
        raise FileExistsError("A project with this name already exists: " + root)
    for d in STANDARD_DIRS:
        os.makedirs(os.path.join(root, d))
    return root


def create_missing_dirs(root):
    created = [d for d in STANDARD_DIRS if not os.path.isdir(os.path.join(root, d))]
    for d in created:
        os.makedirs(os.path.join(root, d))
    return created


def _day_of(path):
    m = DAY_RE.search(os.path.basename(path))
    return m.group() if m else None


def _files(folder):
    """All file names under folder (recursive), or [] if it does not exist."""
    out = []
    for r, _, files in os.walk(folder):
        out += [os.path.join(r, f) for f in files]
    return out


def raw_bins(root):
    """{bin name: set of extensions found in raw/}"""
    bins = {}
    for f in _files(os.path.join(root, "raw")):
        m = BIN_RE.search(os.path.basename(f))
        ext = os.path.splitext(f)[1].lower()
        if m and ext in (".hdr", ".adc", ".roi"):
            bins.setdefault(m.group(), set()).add(ext)
    return bins


def feature_file(features_dir, bin_name):
    """Where WHOI's batch layout puts the feature file of a bin."""
    return os.path.join(features_dir, "features", bin_name[:9], bin_name + "_features_v4.csv")


def complete_bins(root):
    """Bins with all three files (hdr, adc, roi), sorted."""
    return sorted(b for b, exts in raw_bins(root).items() if len(exts) == 3)


def bins_without_features(root):
    """Complete bins (hdr+adc+roi) that have no feature file yet, sorted.
    One listing of the features folder: a file check per bin took minutes on a network drive."""
    suffix = "_features_v4.csv"
    done = {os.path.basename(f)[:-len(suffix)] for f in _files(os.path.join(root, FEATURES_DIR, "features"))
            if f.endswith(suffix)}
    return [b for b in complete_bins(root) if b not in done]


FLOW_ML_PER_MIN = 0.25  # IFCB sample flow rate, as in WHOI's ml_analyzed
STATS_CSV = "bin_stats.csv"
# Per bin, from the .hdr and .adc. stream_*: ROI y (pixels) of the images, median and 10-90 % range; cut_left: images
# starting at x = 0; right_edge / cut_right: the largest x + width of the bin and how many images reach it.
# syringe_ml: SyringeSampleVolume of the .hdr (mL), the volume the run was set to analyse.
STATS_FIELDS = ["rois", "ml_analyzed", "rois_per_ml", "triggers", "images_per_trigger", "run_time", "humidity",
                "humidity_alarm", "stream_y_median", "stream_y_spread", "cut_left", "right_edge", "cut_right",
                "syringe_ml"]
HDR_ONLY = {"syringe_ml"}  # added later: for bins counted before, read from the .hdr alone, not the .adc again


def bin_stats(root, bins, progress=None):
    """{bin: STATS_FIELDS values} from the .adc and .hdr files. Kept in log/bin_stats.csv: each bin is read once
    (again only if it was counted before a field was added), the raw files of a bin do not change.
    progress(done, total) is called after each bin read; saved every 20 bins, so a stopped run loses little."""
    stats = load_stats(root)
    new = [b for b in bins if not stats_complete(stats.get(b))]
    raw = raw_bin_paths(root, new)
    for i, b in enumerate(new, 1):
        old = stats.get(b) or {}
        if old and all(k in old for k in STATS_FIELDS if k not in HDR_ONLY):
            stats[b] = {**old, "syringe_ml": hdr_numbers(raw[b])("SyringeSampleVolume")}
        else:
            stats[b] = read_bin_stats(raw[b], b)
        if i % 20 == 0:
            save_stats(root, stats)
        if progress:
            progress(i, len(new))
    if new:
        save_stats(root, stats)
    return {b: stats[b] for b in bins}


def printer(label):
    """A progress(done, total) for the command line: a line every 10 bins, with the time left."""
    import time
    t0 = time.time()

    def show(done, total):
        if done % 10 == 0 or done == total:
            print(f"{label}: {done} / {total} bins, {time_left(done, total, time.time() - t0)}", flush=True)
    return show


def time_left(done, total, elapsed):
    """'about 12 min left', from the time the first `done` items took."""
    if not done:
        return "estimating the time left…"
    if done >= total:
        return f"done in {elapsed / 60:.0f} min" if elapsed >= 60 else f"done in {round(elapsed)} s"
    s = elapsed / done * (total - done)
    if s < 10:
        return "a few seconds left"
    if s >= 5400:
        return f"about {s / 3600:.1f} h left"
    return f"about {max(1, round(s / 60))} min left" if s >= 60 else f"about {round(s)} s left"


def stats_complete(s):
    return bool(s) and all(k in s for k in STATS_FIELDS)


def hdr_numbers(path):
    """number(key, default=NaN): a value of the .hdr of a bin (path = the bin's files without extension)."""
    from ifcbkit.header import parse_hdr
    with open(path + ".hdr", encoding="utf-8", errors="replace") as f:
        hdr = parse_hdr(f.read().splitlines())

    def number(key, default=float("nan")):
        try:
            return float(hdr.get(key, default))
        except (TypeError, ValueError):
            return default
    return number


def read_bin_stats(path, bin_name):
    """STATS_FIELDS of one bin; path = the bin's files without extension."""
    import statistics
    from ifcbkit.adc import parse_adc_file
    number = hdr_numbers(path)
    run_time = number("runTime", 0)
    ml = FLOW_ML_PER_MIN * (run_time - number("inhibitTime", 0)) / 60
    rois = [r for r in parse_adc_file(bin_name, path + ".adc", extended=True).values() if r["width"] > 0]
    n = len(rois)
    triggers = number("triggerCount")
    ys = sorted(r["y"] for r in rois)
    right = max((r["x"] + r["width"] for r in rois), default=0)
    q = lambda p: ys[min(len(ys) - 1, int(p * len(ys)))] if ys else float("nan")
    return {"rois": n, "ml_analyzed": round(ml, 4), "rois_per_ml": round(n / ml, 2) if ml > 0 else float("nan"),
            "triggers": triggers, "images_per_trigger": round(n / triggers, 3) if triggers > 0 else float("nan"),
            "run_time": round(run_time, 1), "humidity": round(number("humidity"), 2),
            "humidity_alarm": number("HumidityAlarmThreshold(%)", 90.0),
            "stream_y_median": statistics.median(ys) if ys else float("nan"), "stream_y_spread": q(0.9) - q(0.1),
            "cut_left": sum(1 for r in rois if r["x"] <= 0), "right_edge": right,
            "cut_right": sum(1 for r in rois if r["x"] + r["width"] >= right),
            "syringe_ml": number("SyringeSampleVolume")}


def write_status(path, status, tries=10):
    """Writes the progress file of a background run (log/*_status.json) through a .tmp file, so the app never
    reads a half-written file. On a network drive, Windows refuses to replace a file while the app is reading
    it: try again for a few seconds, then skip this update (the next one will do) rather than stop the run."""
    import time
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(status, f)
    for i in range(tries):
        try:
            os.replace(tmp, path)
            return True
        except PermissionError:
            time.sleep(0.5 * (i + 1))
    return False


def load_stats(root):
    path = os.path.join(root, "log", STATS_CSV)
    stats = {}
    if os.path.isfile(path):
        with open(path, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                stats[r["bin"]] = {k: float(v) for k, v in r.items() if k != "bin" and v not in ("", None)}
    return stats


def save_stats(root, stats):
    """Write every bin of `stats` (merge with load_stats() first to keep the others)."""
    path = os.path.join(root, "log", STATS_CSV)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["bin"] + STATS_FIELDS)
        w.writerows([b] + [s.get(k, "") for k in STATS_FIELDS] for b, s in sorted(stats.items()))


def cut_percent(stats):
    """{bin: % of its images touching the left or right edge of the camera field}. The field width is the largest
    x + width of the cruise (it is not in the .hdr), so the right edge counts only for bins that reach it."""
    frame = max((s.get("right_edge", 0) for s in stats.values()), default=0)
    out = {}
    for b, s in stats.items():
        if "cut_left" in s and s.get("rois"):
            cut = s["cut_left"] + (s["cut_right"] if s["right_edge"] >= frame else 0)
            out[b] = round(100 * cut / s["rois"], 2)
    return out


# Which figure shows each check of the bins: qc.py colours a bin only in the figures of its reasons.
# Only the well-established checks, as asked by Emilie Houliez (NOC IFCB QC, 2026-11-23): a check must not use the
# cruise as its reference (a whole cruise with a problem would look good). Removed: few images and small volume
# (against the cruise median; a low count is often real), high humidity (the IFCB stops above its alarm), stream
# shifted or wide and many cut images (against the cruise median: to be developed by the NOC IFCB QC work). Their
# values stay in the QC columns, without a flag.
BIN_CHECKS = {"Incomplete run": "rois_per_ml", "Few images per trigger": "images_per_trigger",
              "Gap in the sampling": "rois_per_ml"}
RUN_TIME_PER_ML = 1200 / 5  # s: a 5 mL sample takes about 1200 s
TRIGGER_LOSS = 0.15  # more than 15 % fewer images than triggers: bad


def check_bins(stats, bin_time):
    """Bins that look failed (incomplete run, images lost) and gaps in the sampling (meaningless for discrete
    samples, e.g. Niskin bottles analysed in batches).
    stats: {bin: bin_stats values}; bin_time: bin name -> datetime. Returns problems like metadata.build."""
    import statistics
    problems = []

    def add(check, b, detail):
        problems.append({"check": check, "bin": b, "spreadsheet row": "", "detail": detail})

    for b, s in sorted(stats.items()):
        def v(k):
            return s.get(k, float("nan"))
        expected = RUN_TIME_PER_ML * v("syringe_ml")  # NaN without SyringeSampleVolume: not checked (flag 9)
        if v("run_time") < 0.5 * expected:
            add("Incomplete run", b, f"{v('run_time'):.0f} s, less than half the {expected:.0f} s expected for a "
                f"{v('syringe_ml'):g} mL sample")
        if s["rois"] < (1 - TRIGGER_LOSS) * v("triggers"):
            add("Few images per trigger", b, f"{s['rois']:.0f} images for {v('triggers'):.0f} triggers: "
                f"{100 * (1 - s['rois'] / v('triggers')):.0f} % fewer images than triggers (more than "
                f"{100 * TRIGGER_LOSS:.0f} %)")
    if len(stats) < 3:
        return problems
    times = sorted((bin_time(b), b) for b in stats)
    steps = [(t2 - t1).total_seconds() for (t1, _), (t2, _) in zip(times, times[1:])]
    gap = max(3600, 3 * statistics.median(steps))  # shortcut: one threshold for the cruise, not per station
    for ((t1, b1), (t2, b2)), s in zip(zip(times, times[1:]), steps):
        if s > gap:
            add("Gap in the sampling", b2, f"{s / 3600:.1f} h without a bin since {b1}")
            problems[-1]["hours"] = round(s / 3600, 1)  # for sample_qc_gap_before_h
    return problems


# The result of the raw data check for EcoTaxa (option B chosen by Amanda, 2026-11-19): one row per bin with the
# values measured and the reasons, in metadata/, joined to the EcoTaxa tables as sample columns. A later QC procedure
# (Emilie's) can write the same file: the EcoTaxa step only reads it.
QC_CSV = "qc_bins.csv"
# (stat, EcoTaxa column, check that flags it or None for a value without flag, flag when it does). IODE scale (BODC):
# 1 good, 3 probably bad (doubtful), 4 bad (failed), 9 missing value.
QC_COLUMNS = [("rois", "sample_acq_qc_images", None, None),  # value only, no flag (see BIN_CHECKS)
              ("ml_analyzed", "sample_acq_volume_analyzed_ml", None, None),
              ("syringe_ml", "sample_acq_qc_syringe_volume_ml", None, None),
              ("run_time", "sample_acq_qc_run_time_s", "Incomplete run", 4),
              ("images_per_trigger", "sample_acq_qc_images_per_trigger", "Few images per trigger", 4),
              ("humidity", "sample_acq_qc_humidity_pct", None, None),
              ("stream_y_median", "sample_acq_qc_stream_y_median_px", None, None),
              ("stream_y_spread", "sample_acq_qc_stream_y_spread_px", None, None),
              ("cut_percent", "sample_acq_qc_cut_images_pct", None, None)]
# Only the checks with a flag go to EcoTaxa (Amanda, 2026-11-24), as sample columns: they are about the whole bin
# (the sample), and SeaSTAR makes one EcoTaxa acquisition per trigger. The other values are shown in step 2 only,
# except the volume analysed: a measure, needed for concentrations. Names (Amanda, 2026-11-25): sample_acq_qc_ for
# the checks of the run in the IFCB, sample_qc_ for the checks of the deployment (the sampling: gap, cruise sheet).
QC_EXPORT = (["sample_acq_volume_analyzed_ml"] + [c for _, c, check, _ in QC_COLUMNS if check]
             + [c + "_flag" for _, c, check, _ in QC_COLUMNS if check]
             + ["sample_qc_gap_before_h", "sample_acq_qc_flag", "sample_acq_qc_flag_causes", "sample_acq_qc_procedure"])
QC_NEEDS = {"run_time": "syringe_ml"}  # the check of a value also needs this one, or its flag is 9
QC_GOOD, QC_MISSING = 1, 9
# Written as the conditions a good bin meets, with what the flags mean: the names of the failures ("Incomplete
# run") here read as a problem of a bin flagged 1 (Amanda, 2026-11-25). The failures are in sample_acq_qc_flag_causes.
QC_PROCEDURE = ("Bin checks of the IFCB app: run time at least half the expected (1200 s per 5 mL of "
                "SyringeSampleVolume) and at least 85 % as many images as triggers. Flag 1 = passed, 4 = failed, "
                "9 = not checkable. NOC IFCB QC procedure in development.")
# Checks of each image (Amanda, 2026-11-25): the columns are in every EcoTaxa table, empty until the NOC IFCB QC
# procedure sets the methods and thresholds (cut image; image outside the stream of particles). Same pattern as the
# sample_acq_qc_ columns: a value and its flag per check, then the worst flag, its causes and the procedure.
OBJECT_QC_COLUMNS = {"object_qc_cut_edge": "t", "object_qc_cut_edge_flag": "f",
                     "object_qc_stream_distance_px": "f", "object_qc_stream_distance_px_flag": "f",
                     "object_qc_flag": "f", "object_qc_flag_causes": "t", "object_qc_procedure": "t"}
# Checks of each trigger (Amanda, 2026-11-25): SeaSTAR makes one EcoTaxa acquisition per trigger, so these are the
# only checks of the acq_ level. Empty, ideas to assess with the NOC IFCB QC procedure: several images for one trigger (acq_qc_images: images of this trigger)
# (particles passed together), saturated scattering or fluorescence signal (none, scattering, fluorescence, both),
# unusual time of flight (ratio to the median of the bin). With the 18 acq_ columns of SeaSTAR: 27, EcoTaxa takes 30.
ACQ_QC_COLUMNS = {"acq_qc_images": "f", "acq_qc_images_flag": "f",
                  "acq_qc_saturated_signal": "t", "acq_qc_saturated_signal_flag": "f",
                  "acq_qc_time_of_flight_ratio": "f", "acq_qc_time_of_flight_ratio_flag": "f",
                  "acq_qc_flag": "f", "acq_qc_flag_causes": "t", "acq_qc_procedure": "t"}
SAMPLE_COLUMNS_MAX = 60  # EcoTaxa accepts at most 60 sample_ columns


def qc_rows(stats, problems):
    """One dict per bin: each value, its flag (<column>_flag), the worst flag of the bin (sample_acq_qc_flag), the reasons
    (sample_acq_qc_flag_causes, "ok" if none; a gap before a bin is not a problem of the bin) and the procedure."""
    cut = cut_percent(stats)
    reasons = {}
    gaps = {p["bin"]: p["hours"] for p in problems if p["check"] == "Gap in the sampling"}
    for p in problems:
        if p["bin"] and p["check"] != "Gap in the sampling":
            reasons.setdefault(p["bin"], []).append(p["check"])
    rows = []
    for b, s in sorted(stats.items()):
        row, flags = {"bin": b}, []
        for key, column, check, bad in QC_COLUMNS:
            v = cut.get(b) if key == "cut_percent" else s.get(key)
            missing = v is None or v != v  # None or NaN
            row[column] = "" if missing else int(v) if float(v).is_integer() else v  # 847, not 847.0
            if check:
                need = s.get(QC_NEEDS.get(key, key))
                missing = missing or need is None or need != need
                row[column + "_flag"] = QC_MISSING if missing else bad if check in reasons.get(b, []) else QC_GOOD
                flags.append(row[column + "_flag"])
        # hours without a bin before this one (gap check), 0 if none, empty if not checked (< 3 bins); no flag
        row["sample_qc_gap_before_h"] = gaps.get(b, 0) if len(stats) >= 3 else ""
        known = [f for f in flags if f != QC_MISSING]
        row["sample_acq_qc_flag"] = max(known) if known else QC_MISSING
        row["sample_acq_qc_flag_causes"] = "; ".join(reasons.get(b, [])) or "ok"
        row["sample_acq_qc_procedure"] = QC_PROCEDURE
        rows.append(row)
    return rows


def qc_flag(row):
    """The worst flag of a row of qc_bins.csv, also in older files: sample_qc_flag (until 2026-11-25) and acq_qc_flag
    (an hour on 2026-11-24)."""
    return row.get("sample_acq_qc_flag") or row.get("sample_qc_flag") or row.get("acq_qc_flag")


def bad_bins(root):
    """Bins flagged bad (sample_acq_qc_flag 4) in metadata/qc_bins.csv, sorted: not processed in steps 3 and 5 unless
    asked (--include-bad, or the box of the app). [] before the raw data check."""
    path = os.path.join(root, "metadata", QC_CSV)
    if not os.path.isfile(path):
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return sorted(r["bin"] for r in csv.DictReader(f) if qc_flag(r) == str(4))


def skip_bad(root, bins, include_bad):
    """(bins to process, bad bins left out)."""
    bad = set() if include_bad else set(bad_bins(root))
    return [b for b in bins if b not in bad], [b for b in bins if b in bad]


def write_qc(root, stats, problems):
    """metadata/qc_bins.csv (the QC_EXPORT columns of qc_rows): put in the EcoTaxa tables as sample columns. Returns its path.
    Written only when its content changes: a newer file makes the zips look older than the raw data check."""
    rows = [{k: r[k] for k in ["bin"] + QC_EXPORT} for r in qc_rows(stats, problems)]
    path = os.path.join(root, "metadata", QC_CSV)
    text = io.StringIO(newline="")
    w = csv.DictWriter(text, list(rows[0]) if rows else ["bin"])
    w.writeheader()
    w.writerows(rows)
    try:
        with open(path, newline="", encoding="utf-8") as f:
            if f.read() == text.getvalue():
                return path
    except OSError:
        pass
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        f.write(text.getvalue())
    return path


def count_sample_columns(root):
    """Most sample_ columns the EcoTaxa tables of the project can get: the metadata, the QC results and the ones
    SeaSTAR writes from its options (id, operator, checked by, project, ship, cruise)."""
    names = set()
    try:
        with open(os.path.join(root, "metadata", "metadata_ecotaxa.json"), encoding="utf-8") as f:
            names |= {c for c in json.load(f).get("columns", {}) if c.startswith("sample_")}
    except (OSError, ValueError):
        pass
    path = os.path.join(root, "metadata", QC_CSV)
    if os.path.isfile(path):
        with open(path, newline="", encoding="utf-8") as f:
            names |= {c for c in next(csv.reader(f), []) if c.startswith("sample_")}
    return len(names | {"sample_id", "sample_operator", "sample_meta_qc_by", "sample_project", "sample_ship", "sample_cruise"})


def qc_bindcols(root):
    """--bindcol options of the columns of metadata/qc_bins.csv (text for the reasons and the procedure), or []."""
    path = os.path.join(root, "metadata", QC_CSV)
    if not os.path.isfile(path):
        return []
    with open(path, newline="", encoding="utf-8") as f:
        header = next(csv.reader(f), [])
    return [f"{c}:{'t' if c in ('sample_acq_qc_flag_causes', 'sample_acq_qc_procedure') else 'f'}:{c}" for c in header if c in QC_EXPORT]


def roi_positions(root, bin_name):
    """[{roi, x, y, width, height}] of the images of a bin, from its .adc (ROI x, y: top left corner, pixels)."""
    from ifcbkit.adc import parse_adc_file
    path = raw_bin_paths(root, [bin_name])[bin_name]
    return [{"roi": t, "x": r["x"], "y": r["y"], "width": r["width"], "height": r["height"]}
            for t, r in sorted(parse_adc_file(bin_name, path + ".adc", extended=True).items()) if r["width"] > 0]


def blob_zip(root, bin_name):
    return os.path.join(root, FEATURES_DIR, "blobs", bin_name[:9], bin_name + "_blobs_v4.zip")


def bins_with_blobs(root):
    """Bins that have a WHOI blobs zip, sorted (one listing of the blobs folder)."""
    suffix = "_blobs_v4.zip"
    return sorted(os.path.basename(f)[:-len(suffix)] for f in _files(os.path.join(root, FEATURES_DIR, "blobs"))
                  if f.endswith(suffix))


def images_and_blobs(root, bin_name, n=12, seed=0):
    """[(roi number, image, blob or None)] for n images of a bin picked at random (seed), read one by one from the
    .roi file rather than the whole bin; the blobs come from the WHOI blobs zip."""
    import random
    import zipfile
    from PIL import Image
    from ifcbkit.adc import parse_adc_file
    from ifcbkit.roi import extract_roi_image
    path = raw_bin_paths(root, [bin_name])[bin_name]
    adc = parse_adc_file(bin_name, path + ".adc", extended=True)
    picked = sorted(random.Random(seed).sample(sorted(adc), min(n, len(adc))))
    blobs = zipfile.ZipFile(blob_zip(root, bin_name)) if os.path.isfile(blob_zip(root, bin_name)) else None
    out = []
    with open(path + ".roi", "rb") as roi:
        for t in picked:
            a = adc[t]
            image = extract_roi_image(roi, a["width"], a["height"], a["offset"]).copy()
            name = f"{bin_name}_{t:05d}.png"
            blob = Image.open(blobs.open(name)).copy() if blobs and name in blobs.namelist() else None
            out.append((t, image, blob))
    return out


def raw_bin_paths(root, bins):
    """{bin: path without extension} for the given bins, from one listing of raw/."""
    wanted, out = set(bins), {}
    for f in _files(os.path.join(root, "raw")):
        name = os.path.splitext(os.path.basename(f))[0]
        if name in wanted:
            out[name] = os.path.splitext(f)[0]
    return out


def scan_name(root):
    """{serial, cruise_id, cruise_name} from ifcb_sn<serial>_<cruise ID>_<name>, or None."""
    m = re.match(r"ifcb_sn([^_]+)_([^_]+)_(.+)$", os.path.basename(os.path.normpath(root)))
    return {"serial": m.group(1), "cruise_id": m.group(2), "cruise_name": m.group(3)} if m else None


def scan_project(root):
    """Summarise an existing project: which standard folders exist, and per day how many
    bins, bad bins (flag 4, no features or zip on purpose), feature files, EcoTaxa zips there are."""
    present = {d: os.path.isdir(os.path.join(root, d)) for d in STANDARD_DIRS}
    days = {}
    bad = set(bad_bins(root))

    def day(d):
        return days.setdefault(d, {"bins": 0, "complete_bins": 0, "bad_bins": 0, "features": 0, "ecotaxa_zips": 0})

    for b, exts in raw_bins(root).items():
        d = day(b[:9])
        d["bins"] += 1
        d["complete_bins"] += len(exts) == 3
        d["bad_bins"] += b in bad

    for f in _files(os.path.join(root, FEATURES_DIR)):
        if f.endswith("_features_v4.csv") and _day_of(f):
            day(_day_of(f))["features"] += 1

    for f in _files(os.path.join(root, "ecotaxa_import_zip_seastar")):
        if f.lower().endswith(".zip") and _day_of(f):
            day(_day_of(f))["ecotaxa_zips"] += 1

    meta_dir = os.path.join(root, "metadata")
    metadata = sorted(os.path.relpath(f, meta_dir).replace("\\", "/") for f in _files(meta_dir) if f.lower().endswith(".csv"))
    info = scan_name(root)
    return {"root": root, "name_info": info, "present": present, "days": dict(sorted(days.items())), "metadata": metadata}


def day_incomplete(d):
    """True when a day of scan_project has incomplete bins, or good bins without a feature file or zip. The bad bins
    may have them or not (--include-bad)."""
    good = d["bins"] - d["bad_bins"]
    return d["complete_bins"] != d["bins"] or not good <= d["features"] <= d["bins"] \
        or not good <= d["ecotaxa_zips"] <= d["bins"]


# ---------------------------------------------------------------- warnings, for the command line
def last_run(root, job):
    """log/<job>_status.json (job: features or ecotaxa), or None if never run."""
    try:
        with open(os.path.join(root, "log", f"{job}_status.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def last_report(root, name):
    """(path, summary line) of the newest log/<name>_<date>.txt (metadata_check or bins_check), or None."""
    log_dir = os.path.join(root, "log")
    files = sorted(f for f in os.listdir(log_dir) if f.startswith(name + "_") and f.endswith(".txt")) \
        if os.path.isdir(log_dir) else []
    if not files:
        return None
    path = os.path.join(log_dir, files[-1])
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()
    return path, lines[2] if len(lines) > 2 else ""


def _some(bins, n=5):
    return ", ".join(bins[:n]) + (f" … (+{len(bins) - n})" if len(bins) > n else "")


def warnings(root, step):
    """What the app warns about before a step, as lines of text: step "features", "ecotaxa" or "all"."""
    out = []
    bins = raw_bins(root)
    incomplete = sorted(b for b, exts in bins.items() if len(exts) < 3)
    if not bins:
        return [f"No IFCB bin in {os.path.join(root, 'raw')}: copy the day folders (DYYYYMMDD) of the IFCB into raw/."]
    if incomplete:
        out.append(f"{len(incomplete)} bin(s) miss a file (.hdr, .adc or .roi) and are left out: " + _some(
            [f"{b} (no {', '.join(e for e in ('.hdr', '.adc', '.roi') if e not in bins[b])})" for b in incomplete]))
    if step in ("features", "all"):
        installed, last = whoi_version(), last_run(root, "features")
        if not installed:
            out.append("WHOI ifcb-features is not installed: pip install -r requirements.txt")
        elif last and last.get("whoi_version") and last["whoi_version"] != installed:
            out.append(f"The installed WHOI ifcb-features ({installed}) is not the one of the last extraction "
                       f"({last['whoi_version']}): the project would mix two versions.")
        latest = latest_whoi_release()
        if installed and latest and not installed.startswith(latest[0]):
            out.append(f"WHOI published ifcb-features {latest[0]} on {latest[1]}; installed: {installed}.")
    for job, what in (("features", "feature extraction"), ("ecotaxa", "EcoTaxa run")):
        last = last_run(root, job)
        if step in (job, "all") and last:
            if not last.get("finished"):
                out.append(f"The last {what} did not finish ({last['done']} / {last['total']} bins), "
                           "or is still running.")
            elif last.get("errors"):
                out.append(f"The last {what} had {len(last['errors'])} failed bin(s): " + _some(last["errors"], 3))
    bad = set(bad_bins(root))
    if bad:
        out.append(f"{len(bad)} bad bin(s) (flag 4 in metadata/{QC_CSV}) are not processed in steps 3 and 5, unless "
                   "asked (--include-bad, or the box of the app): " + _some(sorted(bad)))
    if step in ("ecotaxa", "all"):
        without = [b for b in bins_without_features(root) if b not in bad]
        if without:
            out.append(f"{len(without)} bin(s) without features are left out of the EcoTaxa tables: run "
                       "extract_features.py first. " + _some(without))
        meta_csv = os.path.join(root, "metadata", "metadata_ecotaxa.csv")
        if not os.path.isfile(meta_csv):
            out.append("No metadata file yet (make_metadata.py): the zips would have no sample metadata.")
        else:
            with open(meta_csv, newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            if rows and "sample_metadata_flag" not in rows[0]:
                out.append("The metadata were saved before the metadata flags: save them again (step 4, "
                           "make_metadata.py) to get sample_metadata_flag and sample_metadata_missing.")
            gaps = [r["bin"] for r in rows if r.get("sample_metadata_flag", "1") != "1"]
            added = sorted(set(complete_bins(root)) - {r["bin"] for r in rows})
            if gaps or added:
                out.append(f"{len(gaps) + len(added)} bin(s) with missing or doubtful metadata get their EcoTaxa table "
                           "anyway, flagged (sample_metadata_flag 3 or 9, sample_metadata_missing): "
                           + _some(gaps + added) + ". Correct the spreadsheet, save step 4 again, then make these "
                           "bins again.")
        if not os.path.isfile(os.path.join(root, "metadata", QC_CSV)):
            out.append("No result of the raw data check yet (step 2, make_figures.py): the zips would have no "
                       "sample_acq_qc_ columns.")
        elif len(qc_bindcols(root)) < len(QC_EXPORT):
            out.append("The result of the raw data check was saved in an older format (before 2026-11-24): open step 2 "
                       "again (or run make_figures.py) to write the current sample_acq_qc_ columns.")
        n = count_sample_columns(root)
        if n > SAMPLE_COLUMNS_MAX:
            out.append(f"The EcoTaxa tables would have up to {n} sample_ columns, EcoTaxa accepts {SAMPLE_COLUMNS_MAX}: "
                       "leave some metadata fields out (step 4).")
        for name, script in (("metadata_check", "make_metadata.py"), ("bins_check", "make_figures.py")):
            report = last_report(root, name)
            if report is None:
                out.append(f"No {name.replace('_', ' ')} yet: run {script}.")
            elif not report[1].endswith(", 0 point(s) to check"):
                out.append(f"Last {name.replace('_', ' ')}: {report[1]}, see {report[0]}. Correct them first if you can.")
    return out


def _mtimes(folder, suffix):
    """{bin: modification time} of the files ending with `suffix` under folder. os.scandir: on Windows the times come
    with the folder listing, no extra request per file on the network drive."""
    out, todo = {}, [folder]
    while todo:
        try:
            entries = os.scandir(todo.pop())
        except OSError:
            continue
        with entries:
            for e in entries:
                if e.is_dir():
                    todo.append(e.path)
                elif e.name.endswith(suffix) and BIN_RE.search(e.name):
                    out[BIN_RE.search(e.name).group()] = e.stat().st_mtime
    return out


def _when(t):
    from datetime import datetime
    return datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M") if t else ""


def timeline(root):
    """Where a project stands, with the date of each step, and the steps done out of order: an EcoTaxa zip older than
    its feature file, the metadata or the raw data check; a spreadsheet changed after the metadata were saved; bins
    added after a step. For the Current status page and project_status.py. Reads the folder listings and two CSVs."""
    bins = complete_bins(root)
    meta_dir = os.path.join(root, "metadata")
    feats = _mtimes(os.path.join(root, FEATURES_DIR, "features"), "_features_v4.csv")
    zips = _mtimes(os.path.join(root, "ecotaxa_import_zip_seastar"), ".zip")

    def table(name):
        path = os.path.join(meta_dir, name)
        if not os.path.isfile(path):
            return None, {}
        with open(path, newline="", encoding="utf-8") as f:
            return os.path.getmtime(path), {r["bin"]: r for r in csv.DictReader(f)}

    qc_t, qc = table(QC_CSV)
    meta_t, meta = table("metadata_ecotaxa.csv")
    try:
        with open(os.path.join(meta_dir, "metadata_ecotaxa.json"), encoding="utf-8") as f:
            sheet = json.load(f).get("source_file", "")
    except (OSError, ValueError):
        sheet = ""
    sheet_t = os.path.getmtime(sheet) if sheet and os.path.isfile(sheet) else None

    order = []
    for t, done, what in ((qc_t, qc, "the raw data check: open step 2 again"),
                          (meta_t, meta, "the metadata were saved: save step 4 again")):
        added = [b for b in bins if b not in done]
        if t and added:
            order.append(f"{len(added)} bin(s) added after {what}: " + _some(added))
    if sheet_t and meta_t and sheet_t > meta_t:
        order.append(f"The spreadsheet {os.path.basename(sheet)} was changed ({_when(sheet_t)}) after the metadata "
                     f"were saved ({_when(meta_t)}): save step 4 again, then make the EcoTaxa tables again.")
    made = [b for b in bins if b in zips]
    for what, t_of in (("its feature file", feats.get), ("the metadata", lambda b: meta_t),
                       ("the raw data check", lambda b: qc_t)):
        old = [b for b in made if t_of(b) and zips[b] < t_of(b)]
        if old:
            order.append(f"{len(old)} EcoTaxa zip(s) older than {what}: make them again (step 5, chosen bins): "
                         + _some(old))

    def span(times):
        times = [t for t in times if t]
        return f"{_when(min(times))} → {_when(max(times))}" if times else ""
    flagged = [b for b in bins if b not in meta or meta[b].get("sample_metadata_flag", "1") != "1"] if meta_t else []
    bad = {b for b in bins if qc_flag(qc.get(b, {})) == "4"}
    good = [b for b in bins if b not in bad]
    report = last_report(root, "metadata_check")
    return {"project": os.path.basename(os.path.normpath(root)), "bins": len(bins),
            "days": f"{bins[0][1:9]} → {bins[-1][1:9]}" if bins else "",
            "raw_check": _when(qc_t), "bad_bins": sum(1 for b in bins if qc_flag(qc.get(b, {})) == "4"),
            "good_bins": len(good), "features": sum(1 for b in good if b in feats),
            "bad_features": sum(1 for b in bad if b in feats), "features_made": span(feats.get(b) for b in bins),
            "metadata": _when(meta_t), "metadata_flagged": len(flagged),
            "metadata_check": report[1] if report else "", "zips": sum(1 for b in good if b in zips),
            "bad_zips": sum(1 for b in bad if b in zips), "zips_made": span(zips[b] for b in made),
            "order": order}


def select_bins(root, names):
    """The complete bins of raw/ named in `names` (bin names, or days DYYYYMMDD for all the bins of a day), sorted,
    and the names that match none, each with the closest bins."""
    import difflib
    bins = complete_bins(root)
    chosen, unknown = set(), []
    for n in (n.strip() for n in names):
        hit = [b for b in bins if b == n or (DAY_RE.fullmatch(n) and b.startswith(n + "T"))]
        if hit:
            chosen.update(hit)
        else:
            close = difflib.get_close_matches(n, bins, 3, 0.6)
            unknown.append(f"{n} (closest: {', '.join(close) or 'none'})")
    return sorted(chosen), unknown


def feature_file_problem(root, bin_name, stats=None):
    """Why the feature file of a bin cannot go to EcoTaxa, or None if it looks right: it must have a roi_number
    column and one row per image (the image count of log/bin_stats.csv, when known). SeaSTAR skips a feature file
    without rows and makes the zip anyway, with no features at all."""
    path = feature_file(os.path.join(root, FEATURES_DIR), bin_name)
    try:
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = sum(1 for _ in reader)
            columns = reader.fieldnames or []
    except (OSError, UnicodeDecodeError, csv.Error) as e:
        return f"feature file unreadable ({e})"
    if "roi_number" not in columns:
        return "feature file without a roi_number column"
    if not rows:
        return "feature file without any row"
    images = (stats or {}).get(bin_name, {}).get("rois")
    if images and rows != int(images):
        return f"feature file has {rows} rows for {int(images)} images"
    return None
