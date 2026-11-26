"""BODC: the iFDO metadata spreadsheet of a project and one reference file per bin (make_bodc.py, in the background)."""
import os
import subprocess
import sys

import pandas as pd
import streamlit as st

import common
import make_bodc
import project

st.header("BODC · iFDO submission")
folder = common.current_project()
root = str(folder)
st.caption(f"For the British Oceanographic Data Centre, in `{make_bodc.BODC_DIR}/`: the iFDO spreadsheet (FAIR digital "
           "objects for images: one header for the project, one row per bin) and `refs/<bin>.json` per bin, with a "
           "UUID and the SHA-256 of its .adc, .hdr and .roi. Positions and depths come from step 3.")
if not (folder / "metadata" / "metadata_ecotaxa.csv").is_file():
    st.warning("No metadata yet (step 3): the bins will have no latitude, longitude or altitude.")


def start_bodc(n, names=None, include_bad=False):
    (folder / "log").mkdir(exist_ok=True)
    cmd = [sys.executable, str(common.REPO / "make_bodc.py"), root]
    if names:
        cmd += ["--bins", *names]
    if include_bad:
        cmd.append("--include-bad")
    err = open(folder / "log" / "bodc_errors_python.log", "a", encoding="utf-8")
    proc = subprocess.Popen(cmd, cwd=common.REPO, stdout=subprocess.DEVNULL, stderr=err, creationflags=common.NO_WINDOW)
    common.mark_started(folder, "bodc", proc, n)
    st.session_state["bodc_started"] = True


# ---------------------------------------------------------------- the header
st.subheader("1 · Header of the project")
header = make_bodc.read_header(root)
saved = (folder / make_bodc.BODC_DIR / make_bodc.HEADER_JSON).is_file()
st.caption("Edit the values in the **Value** column, then **Save the header**. A new project starts from the template "
           "(JC282 values) with its own name, a new set UUID and the year of its first bin: fill in at least "
           "image-project, image-context and image-event, and check the abstract. The latitude and longitude bounds "
           "are filled from the bins." + ("" if saved else " :orange[Not saved yet for this project.]"))
table = pd.DataFrame([{"Field": f, "Value": header.get(f, str(v)), "Allowed values": str(a), "Explanation": str(e)}
                      for f, v, a, e in make_bodc.template_header() if f not in make_bodc.BOUNDS])
edited = st.data_editor(table, hide_index=True, width="stretch", height=420, key=f"bodc_header_{folder}",
                        disabled=["Field", "Allowed values", "Explanation"],
                        column_config={"Value": st.column_config.TextColumn(width="large")})
if st.button("Save the header", type="secondary" if saved else "primary"):
    make_bodc.save_header(root, {**header, **dict(zip(edited["Field"], edited["Value"].fillna("")))})
    st.success("Saved. Write the spreadsheet below to put it in the .xlsx.")

# ---------------------------------------------------------------- the files
st.subheader("2 · Reference files and spreadsheet")
bodc_status = common.extraction_status(folder, "bodc")


@st.fragment(run_every=5 if bodc_status and bodc_status["running"] else None)
def bodc_panel():
    status = common.extraction_status(folder, "bodc")
    if status and status["running"]:
        st.session_state["bodc_started"] = True
        done, total = status["done"], status["total"]
        from datetime import datetime
        elapsed = (datetime.now() - datetime.fromisoformat(status["started"])).total_seconds()
        st.progress(done / total if total else 1.0,
                    text=f"Reading the raw files: {done} / {total} bins · {project.time_left(done, total, elapsed)} · "
                         f"started {status['started'].replace('T', ' ')}")
        st.caption("You can close this tab: it goes on as long as this computer stays on and awake.")
        st.button("Stop", key="stop_bodc", on_click=common.stop_extraction, args=(status["pid"],))
        return
    if st.session_state.pop("bodc_started", False):
        st.rerun()
    if status and status["finished"]:
        msg = f"Last run: {status['done']} bin(s) hashed, finished {status['finished'].replace('T', ' ')}"
        if status["errors"]:
            st.warning(msg + f", {len(status['errors'])} error(s):")
            st.code("\n".join(status["errors"]), language=None)
        else:
            st.success(msg + ", no error. The log is in `log/bodc_<date>.log`.")
        if status.get("no_position"):
            with st.expander(f"{len(status['no_position'])} bin(s) without latitude/longitude in step 3"):
                st.code("\n".join(status["no_position"]), language=None)
    elif status:
        st.warning(f"The last run stopped before the end ({status['done']} / {status['total']} bins). Run it again: "
                   "the reference files already made are kept.")

    xlsx = make_bodc.xlsx_path(root)
    if os.path.isfile(xlsx):
        with open(xlsx, "rb") as f:
            st.download_button(f"Download {os.path.basename(xlsx)}", f.read(), file_name=os.path.basename(xlsx))

    todo, done = make_bodc.plan(root)
    names, n, include_bad = common.bin_choice(folder, "bodc", todo,
                                              "All bins, skipping those whose reference file is up to date")
    if names is not None and not n:
        st.caption("Choose days or bins: they are read and hashed again (their UUID is kept).")
        return
    st.markdown(f"**{n} bin{'s' if n != 1 else ''}** to read and hash" + (f" ({len(done)} up to date)" if done else "")
                + ". Every file is read in full: on Y:\\ count about the time of the raw data check. "
                  "At the end the spreadsheet is written again, with every bin and the header saved above.")
    st.button("Write the BODC files" if n else "Write the spreadsheet again", type="primary",
              on_click=start_bodc, args=(n, names, include_bad))


bodc_panel()
