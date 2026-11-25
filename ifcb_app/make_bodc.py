"""BODC submission of an IFCB project: the iFDO metadata spreadsheet (FAIR digital objects for images) and one
reference file per bin, after bodc_submission_generator.py (A. Baldwin, NOC, 2026-02-05, v1.0).

    python make_bodc.py <project folder> [--include-bad] [--bins BIN_OR_DAY ...]

Writes in <project>/bodc/:
- refs/<bin>.json: a UUID for the bin (kept when the file is made again) and the SHA-256 of its .adc, .hdr and .roi,
  with their paths relative to the json;
- bodc_ifdo_metadata_<project folder>.xlsx: bodc_ifdo_template.xlsx with the header of bodc/bodc_header.json
  (edited on the BODC page of the app) and one row per bin: date and time (from the bin name), json, latitude and
  longitude of step 4, altitude = minus the depth of step 4, UUID and SHA-256 of the json.
Reading every .roi to hash it is the slow part: a bin whose json is newer than its three raw files is not read again,
unless chosen with --bins. The bad bins of the raw data check (sample_acq_qc_flag 4) are left out, unless --include-bad.
Progress: log/bodc_status.json (read by the app) and log/bodc_<date>.log.
"""
import argparse
import csv
import hashlib
import json
import os
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import metadata
import project

BODC_DIR = "bodc"
REFS = "refs"
HEADER_JSON = "bodc_header.json"
TEMPLATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bodc_ifdo_template.xlsx")
ITEM_FIELDS = ["image-datetime", "image-handle", "image-latitude", "image-longitude", "image-altitude-meters",
               "image-uuid", "image-hash-sha256"]  # columns B to H of the Event items sheet
FIRST_ITEM_ROW = 4  # rows 1-3: field, allowed values, explanation
# Header fields filled from the items, not on the page
BOUNDS = ("image-set-min-latitude-degrees", "image-set-max-latitude-degrees",
          "image-set-min-longitude-degrees", "image-set-max-longitude-degrees")


def xlsx_path(root):
    return os.path.join(root, BODC_DIR, f"bodc_ifdo_metadata_{os.path.basename(os.path.normpath(root))}.xlsx")


def ref_path(root, bin_name):
    return os.path.join(root, BODC_DIR, REFS, bin_name + ".json")


def template_header():
    """[(field, value, allowed values, explanation)] of the Event header sheet of the template."""
    import openpyxl
    sheet = openpyxl.load_workbook(TEMPLATE)["Event header"]
    return [tuple("" if c is None else c for c in row[:4]) for row in sheet.iter_rows(min_row=2, values_only=True)
            if row[0]]


def read_header(root):
    """{field: value} of bodc/bodc_header.json, or the template's with this project's name, a new set UUID and the
    year of its first bin."""
    try:
        with open(os.path.join(root, BODC_DIR, HEADER_JSON), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        pass
    header = {field: str(value) for field, value, _, _ in template_header()}
    bins = project.complete_bins(root)
    header["image-set-name"] = os.path.basename(os.path.normpath(root))
    header["image-set-uuid"] = str(uuid.uuid4())
    if bins:
        header["image-copyright"] = f"Copyright National Oceanography Centre {bins[0][1:5]}"
    return header


def save_header(root, header):
    os.makedirs(os.path.join(root, BODC_DIR), exist_ok=True)
    with open(os.path.join(root, BODC_DIR, HEADER_JSON), "w", encoding="utf-8") as f:
        json.dump(header, f, indent=1)


def sha256(path):
    m = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):  # 1 MB at a time: a .roi can be large
            m.update(block)
    return m.hexdigest()


def plan(root):
    """(bins to do: json missing or older than one of its raw files, bins whose json is up to date)."""
    raw = os.path.join(root, "raw")
    newest = {}
    for ext in (".adc", ".hdr", ".roi"):
        for b, t in project._mtimes(raw, ext).items():
            newest[b] = max(t, newest.get(b, 0))
    refs = project._mtimes(os.path.join(root, BODC_DIR, REFS), ".json")
    bins = project.complete_bins(root)
    done = [b for b in bins if b in refs and refs[b] >= newest.get(b, 0)]
    return [b for b in bins if b not in set(done)], done


def write_ref(root, bin_name, raw_path):
    """bodc/refs/<bin>.json with the SHA-256 of the three files; keeps the UUID of an older json of the bin."""
    path = ref_path(root, bin_name)
    try:
        with open(path, encoding="utf-8") as f:
            set_uuid = json.load(f)["set_uuid"]
    except (OSError, ValueError, KeyError):
        set_uuid = str(uuid.uuid4())
    ref_dir = os.path.dirname(path)
    files = [{"relative_path": os.path.relpath(raw_path + ext, ref_dir).replace(os.sep, "/"),  # / : read on any system
              "sha256": sha256(raw_path + ext)} for ext in (".adc", ".hdr", ".roi")]
    os.makedirs(ref_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps({"set_uuid": set_uuid, "files": files}, indent=4))


def number(text):
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def items(root, bins):
    """The rows of the Event items sheet for these bins (their json must exist), and the bins without a position."""
    path = os.path.join(root, "metadata", metadata.OUTPUT_CSV)
    meta = {}
    if os.path.isfile(path):
        with open(path, newline="", encoding="utf-8") as f:
            meta = {r["bin"]: r for r in csv.DictReader(f)}
    rows, no_position = [], []
    for b in bins:
        with open(ref_path(root, b), encoding="utf-8") as f:
            set_uuid = json.load(f)["set_uuid"]
        m = meta.get(b, {})
        lat, lon, depth = number(m.get("object_lat")), number(m.get("object_lon")), number(m.get("sample_depth_min"))
        if lat is None or lon is None:
            no_position.append(b)
        rows.append({"image-datetime": b.split("_")[0], "image-handle": f"{REFS}/{b}.json", "image-latitude": lat,
                     "image-longitude": lon, "image-altitude-meters": -depth if depth is not None else None,
                     "image-uuid": set_uuid, "image-hash-sha256": sha256(ref_path(root, b))})
    return rows, no_position


def write_xlsx(root, header, rows):
    """The template with the header values and the rows; the bounding box of the header from the rows."""
    import openpyxl
    header = dict(header)
    for axis in ("latitude", "longitude"):
        values = [r[f"image-{axis}"] for r in rows if r[f"image-{axis}"] is not None]
        header[f"image-set-min-{axis}-degrees"] = min(values) if values else None
        header[f"image-set-max-{axis}-degrees"] = max(values) if values else None
    wb = openpyxl.load_workbook(TEMPLATE)
    sheet = wb["Event header"]
    for r in range(2, sheet.max_row + 1):
        field = sheet.cell(r, 1).value
        if field in header:
            sheet.cell(r, 2).value = header[field] if header[field] != "" else None
    sheet = wb["Event items"]
    for i, row in enumerate(rows):
        for j, field in enumerate(ITEM_FIELDS):
            sheet.cell(FIRST_ITEM_ROW + i, 2 + j).value = row[field]
    path = xlsx_path(root)
    tmp = path + ".tmp.xlsx"  # the old file stays until the new one is complete (and if it is open in Excel)
    wb.save(tmp)
    os.replace(tmp, path)
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("project")
    parser.add_argument("--bins", nargs="+", metavar="BIN_OR_DAY",
                        help="only these bins (names, or days DYYYYMMDD) are hashed again, even if up to date")
    parser.add_argument("--include-bad", action="store_true",
                        help="also the bad bins of the raw data check (sample_acq_qc_flag 4), left out by default")
    parser.add_argument("--workers", type=int, default=4, help="files read at a time (default 4)")
    args = parser.parse_args()

    root = os.path.abspath(args.project)
    log_dir = os.path.join(root, "log")
    os.makedirs(log_dir, exist_ok=True)
    if args.bins:
        todo, unknown = project.select_bins(root, args.bins)
        if unknown:
            sys.exit("Not a complete bin or day of raw/: " + "; ".join(unknown))
    else:
        todo, _ = plan(root)
    every, bad = project.skip_bad(root, project.complete_bins(root), args.include_bad)
    todo = [b for b in todo if b in set(every)]
    raw = project.raw_bin_paths(root, todo)
    status = {"pid": os.getpid(), "started": datetime.now().isoformat(timespec="seconds"), "total": len(todo),
              "done": 0, "errors": [], "finished": None}
    status_path = os.path.join(log_dir, "bodc_status.json")
    log = open(os.path.join(log_dir, f"bodc_{datetime.now():%Y%m%d_%H%M%S}.log"), "a", encoding="utf-8")

    def save():
        project.write_status(status_path, status)

    def say(text):
        log.write(text)
        print(text, end="", flush=True)

    save()
    if bad:
        say(f"{len(bad)} bad bin(s) (flag 4) left out (--include-bad to keep them): {', '.join(bad)}\n")
    say(f"BODC reference files: {len(todo)} bin(s) to hash, {len(every) - len(todo)} up to date\n")
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:  # threads: the time goes in reading the drive
        futures = {pool.submit(write_ref, root, b, raw[b]): b for b in todo}
        for fut in as_completed(futures):
            b = futures[fut]
            try:
                fut.result()
                say(f"[{status['done'] + 1}/{len(todo)}] {b} ok\n")
            except Exception as e:
                status["errors"].append(f"{b}: {e}")
                say(f"[{status['done'] + 1}/{len(todo)}] {b} ERROR {e}\n")
            status["done"] += 1
            log.flush()
            save()
    failed = {e.split(":")[0] for e in status["errors"]}
    with_ref = [b for b in every if b not in failed and os.path.isfile(ref_path(root, b))]
    try:
        rows, no_position = items(root, with_ref)
        header = read_header(root)
        save_header(root, header)  # the set UUID is drawn once, then kept
        path = write_xlsx(root, header, rows)
        say(f"{path}: {len(rows)} bin(s)\n")
        if no_position:
            status["no_position"] = no_position
            say(f"WARNING: {len(no_position)} bin(s) without latitude/longitude in the metadata of step 4: "
                f"{', '.join(no_position)}\n")
    except Exception as e:
        status["errors"].append(f"spreadsheet: {e}")
        say(f"ERROR spreadsheet: {e}\n")
    status["finished"] = datetime.now().isoformat(timespec="seconds")
    save()
    say(f"Finished in {time.time() - t0:.0f} s, {len(status['errors'])} errors\n")
    log.close()


if __name__ == "__main__":
    main()
