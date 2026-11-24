"""EcoTaxa API: live statistics of the IFCB projects in EcoTaxa (read only), with each person's own account."""
import urllib.error

import altair as alt
import pandas as pd
import streamlit as st

import ecotaxa_api as api

st.header("EcoTaxa API · statistics")
st.caption("Live numbers from EcoTaxa: classification progress, validated objects per taxon, living and not living. "
           "Read only: nothing is changed in EcoTaxa. You see the projects your EcoTaxa account has access to.")

# ---------------------------------------------------------------- log in with one's own account
token = api.read_token()
user = None
if token:
    try:
        user = st.cache_data(ttl=600, show_spinner="Connecting to EcoTaxa…")(api.me)(token)
    except api.NotLoggedIn:
        token = None
    except OSError as e:
        st.error(f"EcoTaxa cannot be reached ({e}). Check the internet connection.")
        st.stop()
if not user:
    st.info("Log in with **your own** EcoTaxa account. The password is only sent to EcoTaxa to get a token; it is "
            "not saved. The token stays on this computer (`Documents/.R/ecotaxa/token`, the file the R package "
            "ecotaxarapi uses) until it expires, then this form comes back.")
    with st.form("ecotaxa_login"):
        username = st.text_input("EcoTaxa e-mail")
        password = st.text_input("Password", type="password")
        if st.form_submit_button("Log in", type="primary"):
            try:
                api.login(username.strip(), password)
                st.cache_data.clear()
                st.rerun()
            except urllib.error.HTTPError:
                st.error("EcoTaxa refused this e-mail and password.")
            except OSError as e:
                st.error(f"EcoTaxa cannot be reached ({e}).")
    st.stop()

c1, c2 = st.columns([5, 1], vertical_alignment="center")
c1.caption(f"Connected to EcoTaxa as **{user.get('name', '')}**")
if c2.button("Log out", width="stretch", help="Delete the token of this computer"):
    api.logout()
    st.cache_data.clear()
    st.rerun()

cached = st.cache_data(ttl=600, show_spinner="Asking EcoTaxa…")
# Full project and taxon names on the y axis: by default Altair cuts them at 200 px ("IFCB PAP2025 …")
LABELS = alt.Axis(labelLimit=0, labelFontSize=12, labelPadding=6, labelOverlap=False)
# The colours of EcoTaxa: validated green, predicted blue, dubious orange
STATUS_COLOURS = {"Validated": "#3c9a4a", "Dubious": "#f0a030", "Predicted": "#2a78d6", "Not classified": "#cfcfcf"}
GROUP_COLOURS = {"living": "#3c9a4a", "not-living": "#9e9e9e", "temporary": "#e6c229"}
try:
    # ---------------------------------------------------------------- 1. the projects
    st.subheader("1 · IFCB projects")
    projects = pd.DataFrame(cached(api.projects)(token))
    if projects.empty:
        st.warning("This account sees no IFCB project in EcoTaxa.")
        st.stop()
    c1, c2 = st.columns([2, 1], vertical_alignment="bottom")
    who = c1.text_input("Contact or manager (part of the name)", "Clayton", key="eco_who",
                        help="Empty: every IFCB project this account sees")
    if c2.button("Read EcoTaxa again", width="stretch"):
        st.cache_data.clear()
        st.rerun()
    mask = (projects["contact"].str.contains(who, case=False, regex=False)
            | projects["managers"].str.contains(who, case=False, regex=False)) if who.strip() else projects["projid"] > 0
    shown = projects[mask].sort_values("title")
    if shown.empty:
        st.warning(f"No IFCB project with “{who}” as contact or manager among the {len(projects)} this account sees.")
        st.stop()
    pct = st.column_config.ProgressColumn("% validated", format="%.1f %%", min_value=0, max_value=100)
    split = shown[shown.duplicated("group", keep=False) & shown["part"].notna()]
    merge = st.toggle(f"Merge the projects split in parts ({split['group'].nunique()} split, e.g. “1/4”, “part 2”)",
                      value=True, key="eco_merge", disabled=split.empty,
                      help="A project split in several EcoTaxa projects whose titles differ only by the part number at "
                           "the end is counted as one, below. The table per project stays.")
    st.markdown("**Per EcoTaxa project**")
    st.dataframe(shown[["projid", "title", "part", "objects", "pct_validated", "contact", "managers", "annotators"]].rename(
        columns={"projid": "ID", "title": "Project", "part": "Part", "objects": "Objects", "pct_validated": "% validated",
                 "contact": "Contact", "managers": "Managers", "annotators": "Annotators"}),
        width="stretch", hide_index=True, column_config={"% validated": pct, "Part": st.column_config.NumberColumn(format="%d")})
    # The label of each project in the statistics: its group when merged (the parts add up), else its title
    parts = set(split["projid"])
    label = {r.projid: r.group if merge and r.projid in parts else r.title for r in shown.itertuples()}
    if merge and not split.empty:
        st.markdown("**Split projects, merged**")
        merged = split.assign(validated=split["objects"] * split["pct_validated"] / 100).groupby("group", as_index=False) \
            .agg(Parts=("projid", "count"), IDs=("projid", lambda s: ", ".join(map(str, sorted(s)))),
                 Objects=("objects", "sum"), validated=("validated", "sum"))
        merged["% validated"] = (100 * merged["validated"] / merged["Objects"].where(merged["Objects"] > 0)).round(1)
        st.dataframe(merged.drop(columns="validated").rename(columns={"group": "Project"}), width="stretch",
                     hide_index=True, column_config={"% validated": pct})
    titles = projects.set_index("projid")["title"].to_dict()
    # Text options "<id> · <title>": with the ids as numbers, the choice was lost when the page ran again
    options = [f"{i} · {titles[i]}" for i in shown["projid"]]
    chosen = [int(o.split(" · ")[0]) for o in st.multiselect("Projects for the statistics below", options,
                                                              default=options, key=f"eco_ids_{who}")]
    if not chosen:
        st.stop()
    titles = {p: label.get(p, titles[p]) for p in chosen}  # below: one bar or column per project, or per merged project
    n_shown = len(set(titles.values()))
    # Every project chosen on the y axis, also those without any object of a kind yet
    every = alt.Y("Project:N", title=None, axis=LABELS, scale=alt.Scale(domain=sorted(set(titles.values()))))

    # ---------------------------------------------------------------- 2. classification progress
    st.subheader("2 · Classification progress")
    status = cached(api.project_status)(token, tuple(chosen))
    order = list(api.STATUS.values())
    prog = pd.DataFrame([{"Project": titles[p], "Status": api.STATUS[k], "Objects": v, "rank": order.index(api.STATUS[k])}
                         for p, s in status.items() for k, v in s.items()]) \
        .groupby(["Project", "Status", "rank"], as_index=False)["Objects"].sum()  # the parts of a merged project add up
    st.altair_chart(alt.Chart(prog).mark_bar().encode(
        y=every, x=alt.X("Objects:Q", stack="normalize", title="Share of the objects",
                                                   axis=alt.Axis(format="%")),
        color=alt.Color("Status:N", sort=order, legend=alt.Legend(orient="top", title=None),
                        scale=alt.Scale(domain=order, range=[STATUS_COLOURS[s] for s in order])),
        order=alt.Order("rank:Q"), tooltip=["Project", "Status", alt.Tooltip("Objects:Q", format=",")]
    ).properties(height=max(120, 36 * n_shown)), width="stretch")

    # ---------------------------------------------------------------- 3. validated objects per taxon
    st.subheader("3 · Validated objects per taxon")
    counts = pd.DataFrame(cached(api.taxa_counts)(token, tuple(chosen)))
    if counts.empty or not counts["nb_validated"].sum():
        st.info("No validated object yet in these projects.")
        st.stop()
    names = cached(api.taxa)(token, tuple(sorted(int(t) for t in counts["taxon"].unique())))
    counts["Taxon"] = counts["taxon"].map(lambda t: names.get(t, {}).get("display_name", str(t)))
    counts["Group"] = counts["taxon"].map(lambda t: names.get(t, {}).get("group", ""))
    counts["Lineage"] = counts["taxon"].map(lambda t: names.get(t, {}).get("lineage", ""))
    counts["Project"] = counts["projid"].map(titles)
    valid = counts[counts["nb_validated"] > 0]
    per_taxon = valid.groupby(["Taxon", "Group", "Lineage"], as_index=False)["nb_validated"].sum() \
        .sort_values("nb_validated", ascending=False).rename(columns={"nb_validated": "Validated"})
    top = st.slider("Taxa shown", 5, min(60, len(per_taxon)), min(20, len(per_taxon)), key="eco_top") \
        if len(per_taxon) > 5 else len(per_taxon)
    st.altair_chart(alt.Chart(per_taxon.head(top)).mark_bar(color=STATUS_COLOURS["Validated"]).encode(
        y=alt.Y("Taxon:N", sort="-x", title=None, axis=LABELS), x=alt.X("Validated:Q", title="Validated objects"),
        tooltip=["Taxon", "Lineage", alt.Tooltip("Validated:Q", format=",")]).properties(height=max(150, 22 * top)),
        width="stretch")
    st.caption(f"{len(per_taxon)} taxa with validated objects, {int(per_taxon['Validated'].sum()):,} objects, "
               f"in {valid['projid'].nunique()} EcoTaxa project(s).")
    by_project = valid.pivot_table(index=["Taxon", "Group", "Lineage"], columns="Project", values="nb_validated",
                                   aggfunc="sum", fill_value=0).reset_index()
    with st.expander("Table per taxon and project"):
        st.dataframe(by_project, width="stretch", hide_index=True)
        st.download_button("Download (.csv)", by_project.to_csv(index=False), file_name="ecotaxa_validated_per_taxon.csv")

    # ---------------------------------------------------------------- 4. living and not living
    st.subheader("4 · Living, not living and temporary (validated objects)")
    groups = valid.groupby(["Project", "Group"], as_index=False)["nb_validated"].sum() \
        .rename(columns={"nb_validated": "Validated"})
    st.altair_chart(alt.Chart(groups).mark_bar().encode(
        y=every, x=alt.X("Validated:Q", stack="normalize", title="Share of the validated objects",
                                                   axis=alt.Axis(format="%")),
        color=alt.Color("Group:N", title=None, legend=alt.Legend(orient="top"),
                        scale=alt.Scale(domain=list(GROUP_COLOURS) + sorted(set(groups["Group"]) - set(GROUP_COLOURS)),
                                        range=list(GROUP_COLOURS.values()) + ["#7a4fc4", "#5a6b74", "#b8326b"])),
        tooltip=["Project", "Group", alt.Tooltip("Validated:Q", format=",")]
    ).properties(height=max(120, 36 * n_shown)), width="stretch")
    table = groups.pivot_table(index="Project", columns="Group", values="Validated", aggfunc="sum", fill_value=0)
    table = table.reindex(sorted(set(titles.values())), fill_value=0)  # the projects without validated objects too
    table["% living"] = (100 * (table["living"] if "living" in table else 0) / table.sum(axis=1).where(lambda n: n > 0)).round(1)
    st.dataframe(table.reset_index(), width="stretch", hide_index=True)
    st.caption("Group = the root of the EcoTaxa taxonomy of each validated category: living (green; with the WoRMS "
               "taxa, whose root is “Biota”), not-living (grey), temporary (yellow). "
               "Concentrations (objects per mL) per sample will use `sample_qc_ml_analyzed` of the new tables.")
except api.NotLoggedIn:
    api.logout()
    st.cache_data.clear()
    st.warning("The EcoTaxa token has expired: log in again.")
    st.button("Log in", type="primary")
except OSError as e:
    st.error(f"EcoTaxa cannot be reached ({e}). Check the internet connection, then **Read EcoTaxa again**.")
