"""Pieces shared by the pages of the Process group (steps 1 to 5): the project chosen, cached scans of the network
drive, the background jobs and the progress of each step. The calculations are in project.py, metadata.py, qc.py."""
import json
import subprocess
from pathlib import Path

import pandas as pd
import streamlit as st

import metadata
import project

REPO = Path(__file__).resolve().parent
MINUTES_PER_BIN = 2  # measured: ~2 min for a bin of ~5000 ROIs on one core
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
STEP_PAGES = {1: "steps/step1_project.py", 2: "steps/step2_raw_check.py", 3: "steps/step3_metadata.py",
              4: "steps/step4_features.py", 5: "steps/step5_ecotaxa.py"}
STEP_TITLES = {1: "1 · Project", 2: "2 · Raw data check", 3: "3 · Metadata", 4: "4 · Features", 5: "5 · EcoTaxa tables"}
ICONS = {"done": "✅", "check": "⚠️", "todo": ":material/check_box_outline_blank:", "running": ":material/hourglass_top:"}


def plural_bins(n):
    return f"{n} bin{'s' if n != 1 else ''}"

# Reading a project on the network drive can take a minute: kept in memory, read again by rescan() or after 10 min
scan_project = st.cache_data(ttl=600, show_spinner="Reading the project folder…")(project.scan_project)
bins_without_features = st.cache_data(ttl=600, show_spinner="Looking for the bins without features…")(
    project.bins_without_features)
complete_bins = st.cache_data(ttl=600, show_spinner="Listing the bins…")(project.complete_bins)
bins_with_blobs = st.cache_data(ttl=600, show_spinner="Listing the blobs…")(project.bins_with_blobs)


def rescan():
    st.session_state["rescanned"] = True  # app.py shows a message once the project is read again
    scan_project.clear()
    bins_without_features.clear()
    complete_bins.clear()
    bins_with_blobs.clear()
    progress.clear()


def project_status(folder):
    if not folder:
        return False, "not chosen yet"
    if not folder.is_dir():
        return False, "folder not found"
    missing = [d for d in project.STANDARD_DIRS if not (folder / d).is_dir()]
    if missing:
        return False, f"missing folder{'s' if len(missing) > 1 else ''}: {', '.join(missing)}"
    return True, "complete IFCB project"


LAST_PROJECT = Path.home() / ".ifcb_app_last_project.txt"


def chosen_project():
    """The project chosen in step 1 (Path), or None. Remembered on this computer: a new tab, a reloaded page or the
    app started again opens the last project, without going back to step 1."""
    p = st.session_state.get("project")
    try:
        last = LAST_PROJECT.read_text(encoding="utf-8").strip()
    except OSError:
        last = ""
    if not p and last:
        st.session_state["project"] = p = last
    if not (p and Path(p).is_dir()):
        return None
    if p != last:
        try:
            LAST_PROJECT.write_text(p, encoding="utf-8")
        except OSError:
            pass  # not remembered: only step 1 is asked again next time
    return Path(p)


def current_project():
    """Top of the pages of steps 2 to 5: the project chosen in step 1, or a link to step 1 and stop."""
    folder = chosen_project()
    if not folder:
        st.info("Choose or create the IFCB project first.")
        st.page_link(STEP_PAGES[1], label="Go to 1 · Project", icon="➡️")
        st.stop()
    c1, c2 = st.columns([5, 1], vertical_alignment="center")
    c1.caption(f"Project: **{folder.name}** · `{folder}`")
    c2.button("Rescan", on_click=rescan, width="stretch", help="Read the folders again, e.g. after copying new data")
    return folder


# ---------------------------------------------------------------- background jobs (features, EcoTaxa zips)
def extraction_status(root, job="features"):
    """Content of log/<job>_status.json (features or ecotaxa), with 'running' added; None if never run."""
    try:
        status = json.loads((Path(root) / "log" / f"{job}_status.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    status["running"] = not status["finished"] and pid_alive(status["pid"])
    return status


def mark_started(root, job, proc, total, **extra):
    """Write log/<job>_status.json as soon as the process is started: the script writes its own a few seconds later
    (after listing the folders, slow on Y:), and until then the page would read the last, finished run."""
    if proc is None:
        return
    from datetime import datetime
    status = {"pid": proc.pid, "started": datetime.now().isoformat(timespec="seconds"), "total": total, "done": 0,
              "errors": [], "finished": None, **extra}
    (Path(root) / "log" / f"{job}_status.json").write_text(json.dumps(status), encoding="utf-8")


def pid_alive(pid):
    out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True,
                         creationflags=NO_WINDOW).stdout
    return str(pid) in out


def stop_extraction(pid):
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, creationflags=NO_WINDOW)


def minutes(n_bins, workers):
    """A bin runs on one core only: the cores work in rounds."""
    return -(-n_bins // workers) * MINUTES_PER_BIN


def duration(minutes):
    return f"{minutes / 60:.1f} h" if minutes >= 90 else f"{max(1, round(minutes))} min"


def bin_choice(folder, key, todo, new_label):
    """Which bins to run (steps 4 and 5): (None, n, include_bad) for the bins `todo` not done yet (`new_label`), or
    (the days and bins picked, their number of bins, include_bad) for --bins: those are done again even if already
    done. The bad bins of step 2 (flag 4) are left out unless the box is checked (--include-bad)."""
    radio = st.container()
    bad = set(project.bad_bins(str(folder)))
    include = st.checkbox(f"Include the bad bins ({len(bad)} with flag 4 in the raw data check)", value=False,
                          key=f"{key}_bad_{folder}", disabled=not bad,
                          help="Bins flagged bad in step 2 (incomplete run, images lost) are not processed by default: "
                               "see the table of step 2 for their reasons.")

    def kept(bins):
        return [b for b in bins if include or b not in bad]
    n_new = len(kept(todo))
    mode = radio.radio("Bins", ["new", "chosen"], horizontal=True, key=f"{key}_mode_{folder}", label_visibility="collapsed",
                       format_func=lambda m: f"{new_label} ({n_new} to do)" if m == "new" else "Chosen days or bins, done again")
    if mode == "new":
        return None, n_new, include
    bins = complete_bins(str(folder))
    per_day = {}
    for b in bins:
        per_day[b[:9]] = per_day.get(b[:9], 0) + 1
    c1, c2 = radio.columns(2)  # under the choice, above the box
    days = c1.multiselect("Days", list(per_day), key=f"{key}_days_{folder}",
                          format_func=lambda d: f"{d} ({per_day[d]} bins)")
    picked = c2.multiselect("Bins", [b for b in bins if b[:9] not in days], key=f"{key}_bins_{folder}")
    return days + picked, len(kept([b for b in bins if b[:9] in days] + picked)), include


# ---------------------------------------------------------------- what the steps know of each other
def known_stats(root):
    """Bins counted in log/bin_stats.csv with every value (the others need their .adc read, step 2)."""
    return {b: s for b, s in project.load_stats(str(root)).items() if project.stats_complete(s)}


def metadata_rows(root):
    path = Path(root) / "metadata" / metadata.OUTPUT_CSV
    return pd.read_csv(path, dtype=str, keep_default_na=False).to_dict("records") if path.is_file() else None


def saved_metadata_problems(root):
    """The points of the last metadata check (the report written with the metadata file of step 3)."""
    report = project.last_report(str(root), "metadata_check")
    return metadata.read_report(report[0]) if report and metadata_rows(root) is not None else []


def flag_map(df):
    """The map of steps 2 and 3 on a real background map (st.map: zoom with the mouse wheel), one point per bin coloured
    by qc.map_status (red bad run, orange metadata to check, blue nothing to report); then a line with the numbers of
    bins. The PNG of figures/ is qc.position_map (a fixed map). A pydeck map with tooltips drew huge discs (2026-11-26)."""
    import qc
    _, counts = qc.position_map(df)  # the numbers, with the bins without a position
    if not {"object_lat", "object_lon"} <= set(df.columns):
        return
    pos = df.assign(status=qc.map_status(df)).dropna(subset=["object_lat", "object_lon"])
    if pos.empty:
        return
    colours = dict(qc.STATUSES)
    pos = pos.assign(color=pos["status"].map(colours), top=(pos["status"] != "nothing to report").astype(int))
    pos = pos.sort_values("top")  # flagged bins drawn last, on top
    span_km = 111 * max(pos["object_lon"].max() - pos["object_lon"].min(), pos["object_lat"].max() - pos["object_lat"].min(), 0.01)
    st.map(pos[["object_lat", "object_lon", "color"]], latitude="object_lat", longitude="object_lon", color="color",
           size=span_km * 1000 / 250)  # metres: a point is about 1/250 of the cruise wide (at least 3 pixels)
    st.caption(" · ".join(f"{s}: {n}" for s, n in counts.items()) + ". 🔴 bad run of the raw data check (flag 4) · "
               "🟠 metadata missing or out of range (`sample_metadata_flag` 9 or 3), or a position to check · 🔵 nothing "
               "to report. Mouse wheel to zoom, drag to move; the reasons of each bin are in the tables above. The "
               "positions come from the metadata of step 3 (the columns chosen there, e.g. lat_corrected): the IFCB records none.")


def flagged_bins(root):
    """{bin: [reasons]} of the last metadata check and of the bin checks: what is still to check before EcoTaxa."""
    stats = known_stats(root)
    problems = saved_metadata_problems(root) + (project.check_bins(stats, metadata.bin_time) if stats else [])
    out = {}
    for p in problems:
        if p["bin"] and p["check"] != "Gap in the sampling":
            out.setdefault(p["bin"], []).append(f"{p['check']}: {p['detail']}")
    return out


def _progress(folder):
    """{step: (state, text)} with state in ICONS: shown in the menu and on the page of step 1. From the cached scan
    of the project (one listing of each folder): the menu is drawn on every page."""
    s = scan_project(str(folder))
    days = list(s["days"].values())
    n_bins = sum(d["complete_bins"] for d in days)
    n_all = sum(d["bins"] for d in days)
    n_feat = sum(d["features"] for d in days)
    n_zip = sum(d["ecotaxa_zips"] for d in days)
    out = {1: ("done", f"{plural_bins(n_bins).replace('bin', 'complete bin')} in raw/" + (f", {n_all - n_bins} incomplete" if n_all > n_bins else ""))
           if n_bins else ("todo", "no IFCB bin in raw/ yet")}
    if not n_bins:
        return out
    stats = known_stats(folder)
    counted = sum(1 for b in complete_bins(str(folder)) if b in stats)
    if counted < n_bins:
        out[2] = ("todo", f"{plural_bins(n_bins - counted)} not checked yet")
    else:
        n = len({p["bin"] for p in project.check_bins(stats, metadata.bin_time) if p["check"] != "Gap in the sampling"})
        out[2] = ("check", f"{plural_bins(n)} to look at") if n else ("done", "no bin to look at")
    feat = extraction_status(folder)
    bad = set(project.bad_bins(str(folder)))  # not processed by default: they do not keep a step from being done
    left = sum(1 for b in bins_without_features(str(folder)) if b not in bad)
    if feat and feat.get("running"):
        out[4] = ("running", f"running: {feat['done']} / {plural_bins(feat['total'])}")
    elif left:
        out[4] = ("todo", f"{plural_bins(left)} without features")
    elif feat and feat.get("errors"):
        out[4] = ("check", f"{plural_bins(len(feat['errors']))} failed in the last run")
    else:
        out[4] = ("done", "every bin has its features")
    if metadata_rows(folder) is None:
        out[3] = ("todo", "no metadata saved yet")
    else:
        n = len({p["bin"] for p in saved_metadata_problems(folder) if p["bin"]})
        out[3] = ("check", f"{plural_bins(n)} with a point to check") if n else ("done", "metadata saved, no point to check")
    eco = extraction_status(folder, "ecotaxa")
    if eco and eco.get("running"):
        out[5] = ("running", f"running: {eco['done']} / {plural_bins(eco['total'])}")
    elif n_zip < n_bins - len(bad):  # done when every bin but the bad ones has its zip
        # shortcut: counts, not which bins have a zip; a bad bin made into a zip hides a good bin without one
        out[5] = ("todo", f"{plural_bins(n_bins - len(bad) - n_zip)} without a zip"
                  + (f" ({n_feat - n_zip} ready: features done)" if n_feat > n_zip else ""))
    elif eco and eco.get("errors"):
        out[5] = ("check", f"{plural_bins(len(eco['errors']))} failed in the last run")
    else:
        out[5] = ("done", f"{n_zip} zip{'s' if n_zip != 1 else ''}")
    return out


progress = st.cache_data(ttl=20, show_spinner=False)(_progress)


def states(folder):
    """progress(), plus a run started from step 4 or 5 in this session: shown at once (the cached progress can be
    20 s old), and its end too, even when the page of the step is not open (its progress panel refreshes on its own,
    not the menu)."""
    out = dict(progress(folder))
    for job, step, flag in (("features", 4, "extraction_started"), ("ecotaxa", 5, "ecotaxa_started")):
        if st.session_state.get(flag):
            run = extraction_status(folder, job)
            if run is None or not run.get("finished"):
                out[step] = ("running", f"running: {run['done']} / {plural_bins(run['total'])}" if run else "starting")
            else:
                st.session_state.pop(flag)
                progress.clear()
                return states(folder)
    return out
