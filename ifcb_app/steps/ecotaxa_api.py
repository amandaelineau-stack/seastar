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
    st.dataframe(shown.rename(columns={"projid": "ID", "title": "Project", "contact": "Contact", "managers": "Managers",
                                       "objects": "Objects", "pct_validated": "% validated",
                                       "pct_classified": "% classified"}),
                 width="stretch", hide_index=True,
                 column_config={"% validated": st.column_config.ProgressColumn(format="%.1f %%", min_value=0, max_value=100),
                                "% classified": st.column_config.ProgressColumn(format="%.1f %%", min_value=0, max_value=100)})
    titles = projects.set_index("projid")["title"].to_dict()
    chosen = st.multiselect("Projects for the statistics below", shown["projid"].tolist(), default=shown["projid"].tolist(),
                            format_func=lambda i: f"{i} · {titles[i]}", key=f"eco_ids_{who}")
    if not chosen:
        st.stop()

    # ---------------------------------------------------------------- 2. classification progress
    st.subheader("2 · Classification progress")
    status = cached(api.project_status)(token, tuple(chosen))
    order = list(api.STATUS.values())
    prog = pd.DataFrame([{"Project": titles[p], "Status": api.STATUS[k], "Objects": v, "rank": order.index(api.STATUS[k])}
                         for p, s in status.items() for k, v in s.items()])
    st.altair_chart(alt.Chart(prog).mark_bar().encode(
        y=alt.Y("Project:N", title=None), x=alt.X("Objects:Q", stack="normalize", title="Share of the objects",
                                                   axis=alt.Axis(format="%")),
        color=alt.Color("Status:N", sort=order, legend=alt.Legend(orient="top", title=None),
                        scale=alt.Scale(domain=order, range=["#2a78d6", "#eb6834", "#9fb6c9", "#d9d9d9"])),
        order=alt.Order("rank:Q"), tooltip=["Project", "Status", alt.Tooltip("Objects:Q", format=",")]
    ).properties(height=max(120, 28 * len(chosen))), width="stretch")

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
    st.altair_chart(alt.Chart(per_taxon.head(top)).mark_bar(color="#2a78d6").encode(
        y=alt.Y("Taxon:N", sort="-x", title=None), x=alt.X("Validated:Q", title="Validated objects"),
        tooltip=["Taxon", "Lineage", alt.Tooltip("Validated:Q", format=",")]).properties(height=max(150, 18 * top)),
        width="stretch")
    st.caption(f"{len(per_taxon)} taxa with validated objects, {int(per_taxon['Validated'].sum()):,} objects, "
               f"in {valid['projid'].nunique()} project(s).")
    by_project = valid.pivot_table(index=["Taxon", "Group", "Lineage"], columns="Project", values="nb_validated",
                                   aggfunc="sum", fill_value=0).reset_index()
    with st.expander("Table per taxon and project"):
        st.dataframe(by_project, width="stretch", hide_index=True)
        st.download_button("Download (.csv)", by_project.to_csv(index=False), file_name="ecotaxa_validated_per_taxon.csv")

    # ---------------------------------------------------------------- 4. living and not living
    st.subheader("4 · Living and not living (validated objects)")
    groups = valid.groupby(["Project", "Group"], as_index=False)["nb_validated"].sum() \
        .rename(columns={"nb_validated": "Validated"})
    st.altair_chart(alt.Chart(groups).mark_bar().encode(
        y=alt.Y("Project:N", title=None), x=alt.X("Validated:Q", stack="normalize", title="Share of the validated objects",
                                                   axis=alt.Axis(format="%")),
        color=alt.Color("Group:N", title=None, legend=alt.Legend(orient="top")),
        tooltip=["Project", "Group", alt.Tooltip("Validated:Q", format=",")]
    ).properties(height=max(120, 28 * len(chosen))), width="stretch")
    table = groups.pivot_table(index="Project", columns="Group", values="Validated", aggfunc="sum", fill_value=0)
    table["% living"] = (100 * (table["living"] if "living" in table else 0) / table.sum(axis=1)).round(1)
    st.dataframe(table.reset_index(), width="stretch", hide_index=True)
    st.caption("Group = the root of the EcoTaxa taxonomy of each validated category (living, not-living…). "
               "Concentrations (objects per mL) per sample will use `sample_qc_ml_analyzed` of the new tables.")
except api.NotLoggedIn:
    api.logout()
    st.cache_data.clear()
    st.warning("The EcoTaxa token has expired: log in again.")
    st.button("Log in", type="primary")
except OSError as e:
    st.error(f"EcoTaxa cannot be reached ({e}). Check the internet connection, then **Read EcoTaxa again**.")
