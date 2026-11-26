"""Step 3: the sample metadata (spreadsheet → EcoTaxa columns of metadata_columns.csv), their checks, and the map and
figures of the environmental values, before the EcoTaxa tables. The figures are saved as PNG in figures/."""
import re
from pathlib import Path

import pandas as pd
import streamlit as st

import common
import metadata
import project
import qc

st.header("3 · Sample metadata")
folder = common.current_project()


def browse_file(key):
    """File dialog for the spreadsheet of the sample metadata."""
    import tkinter as tk
    from tkinter import filedialog
    root = tk.Tk()
    root.withdraw()
    root.wm_attributes("-topmost", 1)
    start = st.session_state.get(key) or str(folder / "metadata")
    path = filedialog.askopenfilename(initialdir=str(Path(start).parent) if Path(start).is_file() else start,
                                      parent=root, title="Choose the spreadsheet of the sample metadata",
                                      filetypes=[("Spreadsheets", "*.csv *.xlsx *.xlsm"), ("All files", "*.*")])
    root.destroy()
    if path:
        st.session_state[key] = str(Path(path))


@st.cache_data(show_spinner="Reading the spreadsheet…")
def read_sheet(path, sheet, mtime):  # mtime: read again when the file changes
    return metadata.read_spreadsheet(path, sheet)


@st.cache_data
def sheet_names(path, mtime):
    return pd.ExcelFile(path).sheet_names


st.caption("Choose the spreadsheet of the samples (CSV or Excel), then which of its columns fills each EcoTaxa column. "
           "The EcoTaxa names come from one list shared by all the projects, so every cruise gets the same columns. "
           "A value in **If empty** is used when the spreadsheet has no value for a bin (e.g. depth 5 m). "
           "`sample_date` / `sample_time` (and `object_date` / `object_time`, written by SeaSTAR) are the time "
           "the IFCB analysed the sample, from the bin name.")

meta_dir = folder / "metadata"
saved = metadata.read_choices(meta_dir)
if st.session_state.get("meta_project") != str(folder):  # another project: show its own spreadsheet
    st.session_state["meta_project"] = str(folder)
    st.session_state["meta_file"] = saved.get("source_file", "")
sheet_path = Path(st.session_state["meta_file"]) if st.session_state.get("meta_file") else None
c1, c2 = st.columns([5, 1], vertical_alignment="bottom")
ok = bool(sheet_path and sheet_path.is_file())
c1.text_input(f":green[✅ **Metadata spreadsheet** · found]" if ok else ":red[⚠️ **Metadata spreadsheet** · "
              + ("file not found]" if sheet_path else "not chosen yet]"), key="meta_file",
              help="One row per bin, with a column holding the bin name (e.g. D20250810T164958_IFCB225)")
c2.button("Browse…", key="b_meta_file", on_click=browse_file, args=("meta_file",), width="stretch")
if saved:
    st.caption(f"Metadata last saved {saved['written'].replace('T', ' ')}: "
               f"`metadata/{metadata.OUTPUT_CSV}` ({len(saved['columns'])} columns).")

meta_rows, meta_problems = common.metadata_rows(folder), common.saved_metadata_problems(folder)
if ok:
    mtime = sheet_path.stat().st_mtime
    sheet = None
    if sheet_path.suffix.lower() != ".csv":
        sheet = st.selectbox("Sheet", sheet_names(str(sheet_path), mtime))
    rows, columns = read_sheet(str(sheet_path), sheet, mtime)
    with st.expander(f"Spreadsheet: {len(rows)} rows, {len(columns)} columns"):
        st.dataframe(pd.DataFrame(rows[:50], columns=columns).astype(str), width="stretch", hide_index=True)

    same_file = saved.get("source_file") == str(sheet_path)  # the choices saved apply to that spreadsheet only
    bin_guess = saved.get("bin_column") if same_file else metadata.guess_bin_column(columns, rows)
    bin_column = st.selectbox("Column with the bin name", columns,
                              index=columns.index(bin_guess) if bin_guess in columns else 0,
                              help="Links each row to the images of that bin")

    fields = metadata.load_fields()
    old = saved.get("mapping", {}) if same_file else {}
    # The choices made in the three tables below, per spreadsheet, until the metadata are saved:
    # field -> column ("" = left out; one column per field) and field -> value if empty
    chosen = st.session_state.setdefault("chosen_sources", {}).setdefault(str(sheet_path), {})
    chosen_defaults = st.session_state.setdefault("chosen_defaults", {}).setdefault(str(sheet_path), {})
    sources = {f["field"]: chosen[f["field"]] if f["field"] in chosen else
               old.get(f["field"], {}).get("source", metadata.guess_source(f, columns) or "") or "" for f in fields}
    defaults = {f["field"]: chosen_defaults.get(f["field"], old.get(f["field"], {}).get("default", f["default"])) for f in fields}
    new = metadata.new_columns(columns, rows, bin_column, set(sources.values()))
    TYPES = {"f": "number [f]", "t": "text [t]"}
    state = hash((tuple(new), tuple(sources.items()), tuple(defaults.items()), Path(metadata.FIELDS_CSV).stat().st_mtime))
    YELLOW = "background-color: #fff3b0"
    if st.session_state.get("table_message"):
        st.success(st.session_state.pop("table_message"))

    # ---- 1. columns of the spreadsheet not used yet: map them, then validate
    competing = metadata.competing_columns(new, rows, fields, sources)
    if competing:
        by = {g["field"]: g["ecotaxa_columns"] for g in fields}
        st.warning("**Several columns of the spreadsheet for the same EcoTaxa column**: only one goes to the EcoTaxa "
                   "tables. Check that the one used is the right one (e.g. the corrected values), else tick the other "
                   "in table 1.\n" + "\n".join(
                       f"- `{by[f].replace(';', '`, `')}`: **{cols[0]}** used; also " + ", ".join(f"`{c}`" for c in cols[1:])
                       for f, cols in competing.items()))
    st.markdown(f"**1 · Columns of the spreadsheet not used yet ({len(new)})**")
    if new:
        st.caption("Not in the EcoTaxa tables for now. For each column the closest field of the list is proposed first "
                   "(e.g. `sal_corrected` → `sample_salinity_pss`, in place of the column it uses now); otherwise a new "
                   "EcoTaxa column (`sample_…`): correct its name, type and unit if needed. Tick **Use**, then "
                   "**Use the ticked columns**. Unticked columns stay out of the EcoTaxa tables.")
        proposals = [metadata.propose_field(c, rows, fields) for c in new]
        unused = pd.DataFrame([{
            "Spreadsheet column": c,
            "EcoTaxa columns": p["ecotaxa_column"].replace(";", ", "),
            "Proposed": (f"in the list, instead of {sources[p['field']]}" if sources.get(p["field"]) else "in the list")
                        if p["existing"] else "new EcoTaxa column",
            "Type": TYPES[p["type"]], "Unit": p["unit"], "Use": False} for c, p in zip(new, proposals)])
        with st.form(f"unused_{state}"):
            got = st.data_editor(unused.style.apply(lambda col: [YELLOW] * len(col), subset=["Spreadsheet column", "Proposed"]),
                                 hide_index=True, width="stretch", disabled=["Spreadsheet column", "Proposed"], key="unused_table",
                                 column_config={"Type": st.column_config.SelectboxColumn(options=list(TYPES.values())),
                                                "Use": st.column_config.CheckboxColumn(help="This column fills these EcoTaxa columns")})
            if st.form_submit_button("Use the ticked columns", type="primary"):
                done, errors = [], []
                for r in got.to_dict("records"):
                    if not r["Use"]:
                        continue
                    column = r["Spreadsheet column"]
                    cols = ";".join(c.strip().lower() for c in re.split(r"[;,]", r["EcoTaxa columns"] or "") if c.strip())
                    same = [f["field"] for f in fields if f["ecotaxa_columns"] == cols]
                    if same:  # a field of the list: only its column changes (the one before goes to table 1)
                        name = same[0]
                    else:
                        name = cols.split(";")[0].split("_", 1)[-1]  # a new field is named after its column
                        try:
                            metadata.add_field(name, cols, "f" if r["Type"] == TYPES["f"] else "t", r["Unit"] or "",
                                               description=column)
                        except ValueError as e:
                            errors.append(f"`{column}`: {e}")
                            continue
                    chosen[name] = column
                    done.append(f"`{column}` → `{cols.replace(';', '`, `')}`")
                message = ("Used: " + "; ".join(done) + ". Save the metadata to keep it." if done else "")
                if errors:
                    message += " Not used: " + "; ".join(errors)
                st.session_state["table_message"] = message or "No column ticked."
                st.rerun()
    else:
        st.caption("Every column of the spreadsheet is used (or holds the bin names).")

    # ---- 2. columns used: check them, then validate
    used = [f for f in fields if sources[f["field"]]]
    st.markdown(f"**2 · Columns of the spreadsheet used ({len(used)})**: check them")
    st.caption("Each column fills these EcoTaxa columns. Choose another column if it is not the right one, untick "
               "**Use** to leave it out (it goes back to table 1), then **Apply the changes**.")
    used_table = pd.DataFrame([{
        "Spreadsheet column": sources[f["field"]], "EcoTaxa columns": f["ecotaxa_columns"].replace(";", ", "),
        "Type": TYPES[f["type"]], "Unit": f["unit"], "If empty": defaults[f["field"]], "Use": True} for f in used],
        columns=["Spreadsheet column", "EcoTaxa columns", "Type", "Unit", "If empty", "Use"])
    with st.form(f"used_{state}"):
        got = st.data_editor(used_table, hide_index=True, width="stretch", disabled=["EcoTaxa columns", "Type", "Unit"], key="used_table",
                             column_config={"Spreadsheet column": st.column_config.SelectboxColumn(options=columns),
                                            "If empty": st.column_config.TextColumn(help="Used when the spreadsheet has no value")})
        if st.form_submit_button("Apply the changes"):
            for f, r in zip(used, got.to_dict("records")):
                chosen[f["field"]] = (r["Spreadsheet column"] or "") if r["Use"] else ""
                chosen_defaults[f["field"]] = str(r["If empty"] or "")
            st.session_state["table_message"] = "Changes applied. Save the metadata to keep them."
            st.rerun()

    # ---- 3. fields of the list without a column in this spreadsheet: for information
    missing = [f for f in fields if not sources[f["field"]]]
    st.markdown(f"**3 · EcoTaxa columns of the list not in this spreadsheet ({len(missing)})**: for information")
    st.caption("They get the value **If empty** for every bin (e.g. 5 m for the depth of the underway intake), or stay "
               "empty. To add or remove EcoTaxa columns: **Edit the list** below.")
    missing_table = pd.DataFrame([{
        "EcoTaxa columns": f["ecotaxa_columns"].replace(";", ", "), "Type": TYPES[f["type"]], "Unit": f["unit"],
        "Description": f["description"], "If empty": defaults[f["field"]]} for f in missing],
        columns=["EcoTaxa columns", "Type", "Unit", "Description", "If empty"])
    with st.form(f"missing_{state}"):
        got = st.data_editor(missing_table, hide_index=True, width="stretch", key="missing_table",
                             disabled=["EcoTaxa columns", "Type", "Unit", "Description"])
        if st.form_submit_button("Apply the values if empty"):
            for f, r in zip(missing, got.to_dict("records")):
                chosen_defaults[f["field"]] = str(r["If empty"] or "")
            st.session_state["table_message"] = "Values if empty applied. Save the metadata to keep them."
            st.rerun()

    mapping = {f["field"]: {"source": sources[f["field"]], "default": defaults[f["field"]]} for f in fields}

    bins = common.complete_bins(str(folder))
    out_columns, out, problems = metadata.build(rows, bin_column, mapping, bins)
    st.markdown(f"**{len(out)} bins** · EcoTaxa columns: " + ", ".join(f"`{c}`" for c in out_columns if c != "bin"))
    if problems:
        summary = pd.Series([p["check"] for p in problems]).value_counts()
        st.warning(f"**{len(problems)} point(s) to check** before the EcoTaxa tables: "
                   + " · ".join(f"{check} ({n})" for check, n in summary.items())
                   + ". Correct the spreadsheet, then choose it again.")
        st.dataframe(pd.DataFrame(problems), width="stretch", hide_index=True,
                     column_config={"check": st.column_config.TextColumn("Check", width="medium"),
                                    "detail": st.column_config.TextColumn("Detail", width="large")})
    else:
        st.success("No problem found in the spreadsheet.")
    with st.expander("Preview of the metadata"):
        st.dataframe(pd.DataFrame(out[:200]), width="stretch", hide_index=True)
    if st.button("Save the metadata for the EcoTaxa tables", type="primary", disabled=not out,
                 help=f"Writes metadata/{metadata.OUTPUT_CSV}, used by step 5. Nothing is saved before this click: "
                      "click again after correcting the spreadsheet, then make the zips of the corrected bins again."):
        metadata.write(meta_dir, out_columns, out, mapping, str(sheet_path), bin_column)
        report = metadata.write_report(str(folder / "log"), problems, len(out), str(sheet_path))
        common.progress.clear()
        st.success(f"Saved: `metadata/{metadata.OUTPUT_CSV}`, {len(out)} bins, {len(out_columns) - 1} columns, used by "
                   f"step 5. Check report: `log/{Path(report).name}`.")
    meta_rows, meta_problems = out, problems

with st.expander("✏️ Edit the list of EcoTaxa columns"):
    st.caption(f"The list shared by every project (`{Path(metadata.FIELDS_CSV).name}` of the app). In the toolbar of "
               "the table (top right): **+** adds a row, the **bin** deletes the rows selected with the boxes on their "
               "left; double-click a cell to change it. Then **Save the list**. Several EcoTaxa columns of one field are "
               "separated by `;`. A removed field is no longer proposed; metadata already saved keep it until step 3 is "
               "saved again. Latitude, longitude and depth cannot be removed.")
    list_mtime = Path(metadata.FIELDS_CSV).stat().st_mtime
    list_edited = st.data_editor(
        pd.DataFrame(metadata.load_fields(), columns=metadata.FIELD_KEYS), hide_index=True, width="stretch",
        num_rows="dynamic", key=f"fields_list_{list_mtime}",
        column_config={"field": st.column_config.TextColumn("Field", help="Lower-case letters, digits and _, e.g. oxygen"),
                       "ecotaxa_columns": st.column_config.TextColumn(
                           "EcoTaxa columns", help="Start with sample_ (or object_, acq_, process_), e.g. sample_oxygen_umol_kg"),
                       "type": st.column_config.SelectboxColumn("Type", options=["f", "t"], help="f = number, t = text"),
                       "unit": st.column_config.TextColumn("Unit"),
                       "default": st.column_config.TextColumn("If empty", help="Value used when the spreadsheet has none"),
                       "min": st.column_config.TextColumn("Lowest expected", help="Values outside the range are listed in the checks"),
                       "max": st.column_config.TextColumn("Highest expected"),
                       "description": st.column_config.TextColumn("Description")})
    if st.button("Save the list"):
        try:
            metadata.save_fields(list_edited.to_dict("records"))
        except ValueError as e:
            st.error("Nothing saved: " + str(e))
        else:
            st.session_state["table_message"] = "The list of EcoTaxa columns is saved."
            st.rerun()

# ---------------------------------------------------------------- figures of the metadata
st.subheader("Sampling metadata: map, and each value against time and depth")
st.caption("These values become the `sample_…` and `object_lat` / `object_lon` (also `sample_lat` / `sample_lon`) / `object_depth_…` columns of the EcoTaxa "
           "tables (step 5); the checks of this step are summed up in `sample_metadata_flag` and "
           "`sample_metadata_missing`.")
stats = common.known_stats(folder)
if not meta_rows:
    st.info("The map and the figures appear once a spreadsheet is chosen above.")
    st.stop()
if not stats:
    st.info("The figures need the image counts of each bin: read the raw data in step 2 first.")
    st.page_link(common.STEP_PAGES[2], label="Go to 2 · Raw data check", icon="➡️")
    st.stop()
bin_problems = project.check_bins(stats, metadata.bin_time)
df, variables, depth = qc.frame(stats, meta_rows, meta_problems + bin_problems)
figures = qc.charts(df, variables, depth)
st.caption("A point far from the others is often a typo. In each figure, only the bins flagged for a reason that "
           "figure shows are coloured, and the legend names the reason (e.g. *Out of range* in the salinity, "
           "*Position jump* on the map). Hover a point: *to check* lists every reason of the bin. Bin-name typos and "
           "bins without metadata are in the table above only.")
if qc.MAP_FIGURE in figures:
    _, counts = qc.position_map(df)
    st.altair_chart(figures[qc.MAP_FIGURE][1], width="content")
    st.caption(" · ".join(f"{s}: {n}" for s, n in counts.items()) + ". Red: bad bins of the raw data check (step 2); "
               "orange: values missing or out of range (`sample_metadata_flag` 9 or 3) or a position to check. Hover "
               "a point for its reasons.")
st.caption("One row per variable: against the time the IFCB analysed the bin (left) and against depth (right).")
for c, title, by_depth in variables:
    if not by_depth:
        continue  # the instrument values are in step 2
    left, right = st.columns(2)
    left.altair_chart(figures[qc.figure_name(c, "time")][1], width="stretch")
    if depth:
        right.altair_chart(figures[qc.figure_name(c, "depth")][1], width="stretch")
with st.expander("Table of the values plotted"):
    st.dataframe(df, width="stretch", hide_index=True)
try:  # every figure of steps 2 and 3 as PNG in figures/, written again only when the data change
    with st.spinner("Saving the figures in figures/…"):
        saved_figures = qc.save(folder, figures, df)
    st.caption(f"The figures of steps 2 and 3 are saved as PNG in `figures/` ({len(saved_figures['files'])} files, "
               f"{saved_figures['saved'].replace('T', ' ')}).")
except Exception as e:  # e.g. vl-convert not installed yet: the page still works
    st.caption(f":orange[The figures could not be saved in figures/: {e}]")
st.page_link(common.STEP_PAGES[4], label="Next: 4 · Features", icon="➡️")
