"""Step 2: check the raw data before the features. From the .adc and .hdr of each bin: image counts and volume
analysed, failed bins and gaps, and the instrument (images per trigger, stream of particles, cut images, humidity)."""
import pandas as pd
import streamlit as st

import common
import metadata
import project
import qc

st.header("2 · Raw data check")
folder = common.current_project()
st.caption("Before extracting the features, look for the bins that failed: they can be moved out of `raw/` (e.g. to "
           "`Failed_acquisitions_rawdata`) so that no time is spent on them. Everything here comes from the `.adc` and "
           "`.hdr` files: particle concentration = images / volume analysed (0.25 mL/min × (runTime − inhibitTime)); "
           "the position of each image in the camera field gives the stream of particles and the cut images.")

bins = common.complete_bins(str(folder))
if not bins:
    st.info("No complete bin in `raw/` yet (step 1).")
    st.stop()
known = common.known_stats(folder)
todo = [b for b in bins if b not in known]
if todo and not st.session_state.get(f"count_{folder}"):
    st.info(f"{len(todo)} bin(s) are not read yet, or were read before the instrument values were added. This reads "
            "their `.adc` and `.hdr` files once (about 25 s per bin on a slow network drive), then the values are kept "
            "in `log/bin_stats.csv`. The feature extraction also reads them.")
    if st.button("Read the raw data", type="primary"):
        st.session_state[f"count_{folder}"] = True
        st.rerun()
    st.stop()
if todo:
    import time
    bar, t0 = st.progress(0.0, text=f"Reading the raw data of {len(todo)} bin(s)…"), time.time()

    def show(done, total):
        elapsed = time.time() - t0
        bar.progress(done / total, text=f"Reading the raw data: {done} / {total} bins · "
                                        f"{elapsed / 60:.0f} min so far · {project.time_left(done, total, elapsed)}. "
                                        "Saved every 20 bins: if you leave the page, it goes on from there next time.")
    stats = project.bin_stats(str(folder), bins, progress=show)
    bar.empty()
    common.progress.clear()
else:
    stats = project.bin_stats(str(folder), bins)

bin_problems = project.check_bins(stats, metadata.bin_time)
project.write_qc(str(folder), stats, bin_problems)  # for the EcoTaxa tables, step 5
rows = project.qc_rows(stats, bin_problems)
LABELS = {"sample_acq_qc_images": "Images", "sample_acq_volume_analyzed_ml": "mL analysed",
          "sample_acq_qc_syringe_volume_ml": "Syringe volume (mL)", "sample_acq_qc_run_time_s": "Run time (s)",
          "sample_acq_qc_images_per_trigger": "Images per trigger", "sample_acq_qc_humidity_pct": "Humidity (%)",
          "sample_acq_qc_stream_y_median_px": "Stream position, y (px)", "sample_acq_qc_stream_y_spread_px": "Stream width, y (px)",
          "sample_acq_qc_cut_images_pct": "Cut images (%)"}
FLAG_COLOURS = {3: "background-color: rgba(235,104,52,.30)", 4: "background-color: rgba(200,40,40,.40)",
                9: "background-color: rgba(120,120,120,.15)"}
bad = [r for r in rows if r["sample_acq_qc_flag"] in (3, 4)]
gaps = [p for p in bin_problems if p["check"] == "Gap in the sampling"]
if bad:
    st.warning(f"**{len(bad)} bad bin(s)** (flag 4) out of {len(rows)}. "
               "Failed acquisitions can be moved out of `raw/` before the features; then **Rescan**.")
else:
    st.success(f"No bin to look at among the {len(rows)} bins.")
show_all = st.toggle("Show every bin", value=not bad, key=f"qc_all_{folder}")
shown = rows if show_all else bad
if shown:
    table = pd.DataFrame([{"Bin": r["bin"], **{LABELS[c]: r[c] for _, c, _, _ in project.QC_COLUMNS},
                           "Flag": r["sample_acq_qc_flag"], "Reasons": r["sample_acq_qc_flag_causes"]} for r in shown])
    flags = pd.DataFrame([{LABELS[c]: r[c + "_flag"] for _, c, check, _ in project.QC_COLUMNS if check}
                          | {"Flag": r["sample_acq_qc_flag"]} for r in shown])
    styled = table.style.apply(lambda col: [FLAG_COLOURS.get(f, "") for f in flags[col.name]]
                               if col.name in flags.columns else [""] * len(col), axis=0)
    st.dataframe(styled, width="stretch", hide_index=True,
                 column_config={"Reasons": st.column_config.TextColumn(width="large")})
st.caption("One row per bin, one column per value measured: **red** = bad (flag 4), grey = not checked (flag 9); "
           "*Flag* is the worst of the bin (1 = good). Only the run time and the images per trigger are checked, the "
           "other values have no flag (the NOC IFCB QC procedure is in development). Flags of the IODE scale, "
           f"used by BODC. The checks with a flag are saved in `metadata/{project.QC_CSV}` and put in the EcoTaxa tables "
           "(step 5) as sample columns: the volume analysed, each checked value (`sample_acq_qc_run_time_s`, `sample_acq_qc_images_per_trigger`) "
           "with its flag (`…_flag`), the worst flag (`sample_acq_qc_flag`), the reasons (`sample_acq_qc_flag_causes`) and the procedure, "
           "so that doubtful bins are known before the classification and can be filtered in EcoTaxa. The values "
           "without a flag are shown here only.")
if bin_problems:
    with st.expander(f"Why each bin is flagged ({len(bin_problems)} points, with the values of the cruise)"
                     + (f", and {len(gaps)} gap(s) in the sampling" if gaps else "")):
        st.dataframe(pd.DataFrame(bin_problems).drop(columns=["spreadsheet row", "hours"], errors="ignore"), width="stretch", hide_index=True,
                     column_config={"check": st.column_config.TextColumn("Check"), "bin": st.column_config.TextColumn("Bin"),
                                    "detail": st.column_config.TextColumn("Detail", width="large")})
with st.expander("What each check means"):
    st.markdown("""
Only the well-established checks, none of them compared with the rest of the cruise (a whole cruise with a problem
would look good). The NOC IFCB QC procedure, in development, will add more.

- **Incomplete run** (bad, flag 4): the run time is less than half the expected one. A 5 mL sample takes about
  1200 s; the expected time is scaled to the `SyringeSampleVolume` of the `.hdr` (600 s for 5 mL, 300 s for 2.5 mL).
- **Few images per trigger** (bad, flag 4): more than 15 % fewer images than triggers, images lost.
- **Gap in the sampling**: no bin for a long time (instrument stopped, or bins missing from `raw/`); not a flag, and
  meaningless for discrete samples (e.g. Niskin bottles analysed in batches).
- The other values (images, volume, humidity, position and width of the stream, cut images) are given without a
  flag, and the ROI x / ROI y plot below shows the flow of each bin.""")

df, variables, _ = qc.frame(stats, None, bin_problems)
figures = qc.charts(df, variables, None)
HOW_TO_READ = ("Each point is a bin, against the time the IFCB analysed it. Only the bins flagged for a reason the figure "
               "shows are coloured, and the legend names the reason. Hover a point: *to check* lists every reason of the "
               "bin. Drag to pan, Shift + mouse wheel to zoom, double-click to reset.")
RUN = ["rois_per_ml", "images_per_trigger"]  # the values behind the sample_acq_ columns of the EcoTaxa tables
names = [c for c, _, _ in variables]
groups = [("Run in the IFCB: concentration, images and triggers",
           "In the EcoTaxa tables (step 5): `sample_acq_volume_analyzed_ml` (the volume analysed, from which the "
           "concentration is computed) and "
           "`sample_acq_qc_images_per_trigger` with its flag, summed up in `sample_acq_qc_flag`, "
           "`sample_acq_qc_flag_causes` and `sample_acq_qc_procedure`. The run time (`sample_acq_qc_run_time_s`) is "
           "checked in the table above.", [c for c in names if c in RUN]),
          ("Flow, camera field and humidity: values without a flag",
           "Shown here only, not in the EcoTaxa tables yet: once the NOC IFCB QC procedure sets their limits, they will "
           "be checks of the run, i.e. `sample_acq_qc_…` columns with their flag.", [c for c in names if c not in RUN])]
for title, where, cols in groups:
    if not cols:
        continue
    st.subheader(title)
    st.caption(where + " " + HOW_TO_READ)
    for i in range(0, len(cols), 2):
        for col, c in zip(st.columns(2), cols[i:i + 2]):
            col.altair_chart(figures[qc.figure_name(c, "time")][1], width="stretch")

st.subheader("Flow: position of the images in the camera field")
st.caption("ROI x against ROI y of every image of a bin, as the RoiXY graph of IFCBacquire and the plot of the IFCB "
           "dashboard. The images should form one compact band: spread out, or moved, they point to a bad flow. A point "
           "is the top left corner of an image; orange: the image touches the left or right edge, so it is cut (a long "
           "image starting in the middle can reach the right edge). The bins with the most cut images come first.")
cut = project.cut_percent(stats)
choices = sorted(bins, key=lambda b: -cut.get(b, 0))
roi_bin = st.selectbox("Bin", choices, key=f"roi_bin_{folder}",
                       format_func=lambda b: f"{b}  ({cut.get(b, 0):.1f} % cut images)")
frame_right = max(s.get("right_edge", 0) for s in stats.values())
with st.spinner("Reading the .adc file…"):
    rois = project.roi_positions(str(folder), roi_bin)
if rois:
    st.altair_chart(qc.zoomable(qc.roi_chart(rois, frame_right)), width="stretch")
    s = stats[roi_bin]
    st.caption(f"{len(rois)} images · stream centred at y = {s.get('stream_y_median', float('nan')):.0f} px, "
               f"{s.get('stream_y_spread', float('nan')):.0f} px wide (10 to 90 %) · "
               f"{df.set_index('bin').at[roi_bin, 'cut_percent'] or 0:.1f} % of the images touch the edge "
               f"(camera field {frame_right:.0f} px wide, the largest x + width of the cruise)")
with st.expander("Table of the values"):
    st.dataframe(df, width="stretch", hide_index=True)
st.page_link(common.STEP_PAGES[3], label="Next: 3 · Features", icon="➡️")
