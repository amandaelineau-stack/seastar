"""Current status: where one or several projects stand, with the date of each step and the steps done out of order."""
import pandas as pd
import streamlit as st

import common
import project
from ui import DEFAULT_PARENT, folder_input

st.title("Current status")
st.caption("For each project chosen: the bins, the raw data check, the features, the metadata and the EcoTaxa tables, "
           "with their dates, and what was done out of order (e.g. EcoTaxa tables made before the last metadata). "
           "Reads the folder listings only; nothing is written.")

timeline = st.cache_data(ttl=600, show_spinner=False)(project.timeline)
current = common.chosen_project()
st.session_state.setdefault("status_parent", str(current.parent) if current else DEFAULT_PARENT)
parent = folder_input("Folder of the IFCB projects", "status_parent", "The folder that holds the ifcb_sn… projects, e.g. Y:\\")
projects = sorted(d.name for d in parent.iterdir() if d.is_dir() and d.name.lower().startswith("ifcb_sn")) \
    if parent and parent.is_dir() else []
default = [current.name] if current and parent and current.parent == parent and current.name in projects else []
c1, c2 = st.columns([5, 1], vertical_alignment="bottom")
chosen = c1.multiselect("Projects", projects, default=default, key=f"status_projects_{parent}",
                        placeholder="Choose one or several projects")
c2.button("Read again", on_click=timeline.clear, width="stretch", help="Read the folders again, e.g. after a step")
if not chosen:
    st.info("Choose one or several projects.")
    st.stop()


def done(n, total, n_bad, bad):
    """n good bins done of total, and the bad bins (flag 4, not processed by default)."""
    return (f"{'✅' if total and n == total else '⚠️' if n else '☐'} {n} / {total}"
            + (f" · bad: {n_bad} / {bad}" if bad else ""))


rows = []
bar = st.progress(0.0)
for i, name in enumerate(chosen):
    bar.progress(i / len(chosen), text=f"Reading {name} ({i + 1} / {len(chosen)})…")
    t = timeline(str(parent / name))
    checked = t["metadata_check"].endswith(", 0 point(s) to check")
    meta = ("☐ not saved" if not t["metadata"] else
            f"{'✅' if checked and not t['metadata_flagged'] else '⚠️'} {t['metadata']}"
            + (f" · {t['metadata_flagged']} bin(s) flagged" if t["metadata_flagged"] else ""))
    rows.append({"Project": name, "Bins": t["bins"], "Days": t["days"],
                 "Raw data check": (f"{'⚠️' if t['bad_bins'] else '✅'} {t['raw_check']}"
                                    + (f" · {t['bad_bins']} bad" if t["bad_bins"] else "")) if t["raw_check"] else "☐",
                 "Features": done(t["features"], t["good_bins"], t["bad_features"], t["bad_bins"]),
                 "Features made": t["features_made"],
                 "Metadata": meta, "Last metadata check": t["metadata_check"],
                 "EcoTaxa tables": done(t["zips"], t["good_bins"], t["bad_zips"], t["bad_bins"]),
                 "Tables made": t["zips_made"],
                 "Order of the steps": f"⚠️ {len(t['order'])} to look at" if t["order"] else "✅",
                 "_order": t["order"]})
bar.empty()

table = pd.DataFrame(rows)
st.dataframe(table.drop(columns="_order"), width="stretch", hide_index=True,
             column_config={"Project": st.column_config.TextColumn(pinned=True)})
st.caption("✅ done · ⚠️ to look at · ☐ not done. *Features* and *EcoTaxa tables*: bins done / complete bins, without "
           "the bad bins of the raw data check (flag 4, not processed unless asked), counted apart. Dates: "
           "when the files were written (oldest → newest). *Metadata flagged*: bins not in the spreadsheet or with an "
           "empty or doubtful value (`sample_metadata_flag` 3 or 9 in the EcoTaxa tables).")

late = [r for r in rows if r["_order"]]
if late:
    st.subheader("Order of the steps")
    for r in late:
        with st.expander(f"⚠️ {r['Project']}: {len(r['_order'])} point(s)", expanded=len(late) == 1):
            st.markdown("\n".join(f"- {line}" for line in r["_order"]))
else:
    st.success("The steps were done in order for every project chosen.")
