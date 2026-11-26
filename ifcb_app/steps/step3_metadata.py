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
    chosen = st.session_state.setdefault("chosen_sources", {}).setdefault(str(sheet_path), {})  # field -> column, "Use it"
    sources = {f["field"]: chosen.get(f["field"]) or old.get(f["field"], {}).get("source", metadata.guess_source(f, columns) or "") or ""
               for f in fields}
    new = metadata.new_columns(columns, rows, bin_column, set(sources.values()))
    TYPES = {"f": "number [f]", "t": "text [t]"}
    proposed = [{"New?": "🟡 unused · " + ("in the list" if p["existing"] else "new field"), **{k: v for k, v in zip(
        ["Field", "EcoTaxa columns", "Type", "Unit"],
        [p["field"], p["ecotaxa_column"].replace(";", ", "), TYPES[p["type"]], p["unit"]])},
        "From spreadsheet column": c, "If empty": "", "Use it": False}
        for c in new for p in [metadata.propose_field(c, rows, fields)]]
    table = pd.DataFrame(proposed + [{
        "New?": "",
        "Field": f["field"],
        "EcoTaxa columns": f["ecotaxa_columns"].replace(";", ", "),
        "Type": TYPES[f["type"]],
        "Unit": f["unit"],
        "From spreadsheet column": sources[f["field"]],
        "If empty": old.get(f["field"], {}).get("default", f["default"]),
        "Use it": None,
    } for f in fields])
    if st.session_state.get("table_message"):
        st.success(st.session_state.pop("table_message"))
    if new:
        st.info(f"**{len(new)} column(s) of the spreadsheet are not used** (yellow rows, at the top). For each, the "
                "closest field of the list is proposed first (*in the list*, e.g. `sal_corrected` → salinity): tick "
                "**Use it** and it fills that field instead of the column used now. Otherwise a new field is proposed "
                "(*new field*): correct its name, type and unit if needed, then tick **Use it** to add it to the list. "
                "Unticked columns are left out of the EcoTaxa tables.")
    # only the columns that cannot be edited take a colour in st.data_editor: "New?" carries the yellow
    styled = table.style.apply(lambda col: ["background-color: #fff3b0" if v else "" for v in col], subset=["New?"])
    edited = st.data_editor(  # the key changes with the yellow rows and the list: older edits would land on other rows
        styled, hide_index=True, width="stretch",
        key=f"mapping_{sheet_path}_{sheet}_{Path(metadata.FIELDS_CSV).stat().st_mtime}_{hash(tuple(new))}",
        disabled=["New?"],
        column_config={"New?": st.column_config.TextColumn(width="small"),
                       "Type": st.column_config.SelectboxColumn(options=list(TYPES.values())),
                       "From spreadsheet column": st.column_config.SelectboxColumn(options=[""] + columns),
                       "If empty": st.column_config.TextColumn(help="Constant used when the spreadsheet has no value"),
                       "Use it": st.column_config.CheckboxColumn(
                           help="Yellow rows only: this column fills the field (a new field is first added to the "
                                "list shared by every project)")})
    records = edited.to_dict("records")
    by_name = {f["field"]: f for f in fields}
    for r in records[:len(new)]:
        if not r["Use it"]:
            continue
        name, column = (r["Field"] or "").strip().lower(), r["From spreadsheet column"]
        cols = ";".join(c.strip() for c in re.split(r"[;,]", r["EcoTaxa columns"] or "") if c.strip())
        if name in by_name and cols == by_name[name]["ecotaxa_columns"]:  # a field of the list: only its source changes
            st.session_state["table_message"] = (f"`{column}` now fills {name}" + (f" instead of `{sources[name]}`" if sources[name] else "")
                                                 + ". Save the metadata to keep it.")
        else:
            try:
                metadata.add_field(name, cols, "f" if r["Type"] == TYPES["f"] else "t", r["Unit"] or "",
                                   str(r["If empty"] or ""), column or "")
            except ValueError as e:
                st.error(f"{column}: {e}")
                continue
            st.session_state["table_message"] = f"Added `{cols}` to the list of every project, filled by `{column}`."
        chosen[name] = column
        st.rerun()
    shared = ["Field", "EcoTaxa columns", "Type", "Unit"]
    if any(r[k] != t[k] for r, t in zip(records[len(new):], table.to_dict("records")[len(new):]) for k in shared):
        st.info("Only the yellow rows can be renamed: the other fields are shared by every project "
                "(**Edit the list** below); their changes here are not used.")
    mapping = {t["Field"]: {"source": r["From spreadsheet column"] or "", "default": str(r["If empty"] or "")}
               for r, t in zip(records[len(new):], table.to_dict("records")[len(new):])}

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
    pos = df.assign(why=qc.why(df, qc.MAP)).dropna(subset=["object_lat", "object_lon"])
    st.map(pos.assign(color=[qc.SERIES if w == "ok" else qc.TO_CHECK for w in pos["why"]]),
           latitude="object_lat", longitude="object_lon", color="color", size=400)
    st.caption(f"{len(pos)} bin{'s' if len(pos) > 1 else ''} with a position · blue: no problem · orange: a position "
               "to check (position jump, latitude or longitude out of range), named in the figure below")
    st.altair_chart(figures[qc.MAP_FIGURE][1], width="stretch")
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
