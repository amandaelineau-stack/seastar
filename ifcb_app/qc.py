"""Quality check figures of a project (no UI code here): used by the pages of steps 2 and 3 and by make_figures.py."""
import functools
import hashlib
import json
import math
import os
from datetime import datetime
from pathlib import Path

import pandas as pd

import metadata
import project

SERIES = "#2a78d6"  # blue: bins without a point to check in this figure
# One colour per reason shown in a figure (orange first): the legend names the reason.
REASON_COLOURS = ["#eb6834", "#b8326b", "#7a4fc4", "#9a7b00", "#1c8a7a", "#5a6b74"]
TO_CHECK = REASON_COLOURS[0]

# Instrument values of each bin, plotted against time only.
INSTRUMENT = [("images_per_trigger", "Images per trigger"),
              ("stream_y_median", "Stream position: median ROI y (pixels)"),
              ("stream_y_spread", "Stream width: ROI y from 10 to 90 % (pixels)"),
              ("cut_percent", "Images touching the edge of the camera field (%)"),
              ("humidity", "Humidity inside the IFCB (%)")]
MAP = "map"
POSITION = ("object_lat", "object_lon", "sample_lat", "sample_lon")  # on the map, not plotted against time
# A point is the top left corner of an image: a long image starting in the middle can reach the right edge.
EDGE = "touches the edge (x = 0, or x + width = right edge)"


def reasons_by_figure(problems):
    """{figure key: {bin: "reason; reason"}}: a bin is coloured only in the figures that show why it is flagged.
    Figure keys: a column plotted (e.g. rois_per_ml, sample_salinity_pss) or MAP. Problems without a figure (bin-name
    typos, bins without metadata…) are only in the tables."""
    columns_of = {f["field"]: f["ecotaxa_columns"].split(";") for f in metadata.load_fields()}
    out = {}
    for p in problems:
        if not p.get("bin"):
            continue
        keys = []
        if p["check"] in project.BIN_CHECKS:
            keys = [project.BIN_CHECKS[p["check"]]]
        elif p["check"] == "Position jump":
            keys = [MAP]
        elif p["check"] in ("Out of range", "Not a number"):
            columns = columns_of.get(p["detail"].split(" = ")[0], [])
            keys = list(dict.fromkeys(MAP if c in POSITION else c for c in columns))
        for k in keys:
            reasons = out.setdefault(k, {}).setdefault(p["bin"], [])
            if p["check"] not in reasons:
                reasons.append(p["check"])
    return {k: {b: "; ".join(r) for b, r in v.items()} for k, v in out.items()}


def frame(stats, meta_rows, problems):
    """(table of the bins with their values and reasons, [(column, title, against depth too)], depth column or None).
    problems: those of metadata.build and project.check_bins."""
    cut = project.cut_percent(stats)
    df = pd.DataFrame([{"bin": b, "time": metadata.bin_time(b), **s, "cut_percent": cut.get(b)}
                       for b, s in stats.items()])
    fields = metadata.load_fields()
    labels = {c: f"{f['field']} ({f['unit']})" if f["unit"] else f["field"]
              for f in fields for c in f["ecotaxa_columns"].split(";")}
    numeric = [c for f in fields for c in f["ecotaxa_columns"].split(";") if f["type"] == "f"]
    if meta_rows:
        meta = pd.DataFrame(meta_rows)
        keep = [c for c in meta.columns if c in numeric]
        meta[keep] = meta[keep].apply(pd.to_numeric, errors="coerce")
        flag = ["sample_metadata_flag"] if "sample_metadata_flag" in meta.columns else []  # for the map
        df = df.merge(meta[["bin"] + keep + flag].astype({c: str for c in flag}), on="bin", how="left")
    every = {}
    for p in problems:
        if p.get("bin"):
            every.setdefault(p["bin"], []).append(f"{p['check']}: {p['detail']}")
    df["to check"] = [" | ".join(every.get(b, [])) for b in df["bin"]]
    for key, by_bin in reasons_by_figure(problems).items():
        df[f"why {key}"] = [by_bin.get(b, "") for b in df["bin"]]
    depth = "sample_depth_min" if "sample_depth_min" in df.columns and df["sample_depth_min"].notna().any() else None
    variables = [("rois_per_ml", "Particle concentration (images mL-1)", True)] + [
        (c, labels[c], True) for c in df.columns if c in numeric and c.startswith("sample_") and "depth" not in c and c not in POSITION
        and df[c].notna().any()] + [(c, t, False) for c, t in INSTRUMENT if c in df.columns and df[c].notna().any()]
    return df, variables, depth


# When a bin has several reasons in one figure, its colour is the first of this list (the tooltip gives them all):
# one colour per reason keeps the legend short, combinations made it too long to read.
PRIORITY = ["Incomplete run", "Few images per trigger", "Out of range", "Not a number", "Position jump",
            "Gap in the sampling"]


def why(df, *keys):
    """The main reason of each bin for the figures `keys` (e.g. the variable and the depth), "ok" when none."""
    cols = [f"why {k}" for k in keys if k and f"why {k}" in df.columns]
    if not cols:
        return pd.Series("ok", index=df.index)
    rank = {c: i for i, c in enumerate(PRIORITY)}

    def main(row):
        reasons = [x for v in row for x in v.split("; ") if x]
        return min(reasons, key=lambda r: rank.get(r, len(rank))) if reasons else "ok"
    return df[cols].apply(main, axis=1)


def colour(values):
    """Altair colour: blue for ok, one colour per reason, legend titled "To check" on several lines if needed."""
    import altair as alt
    reasons = sorted(set(values) - {"ok"}, key=lambda r: PRIORITY.index(r) if r in PRIORITY else len(PRIORITY))
    rng = [SERIES] + [REASON_COLOURS[i % len(REASON_COLOURS)] for i in range(len(reasons))]
    return alt.Color("why:N", scale=alt.Scale(domain=["ok"] + reasons, range=rng),
                     legend=alt.Legend(title="To check", orient="top", columns=3, labelLimit=180,
                                       symbolSize=60) if reasons else None)


BAD = "#c62828"  # red: a bad bin of the raw data check (flag 4)
STATUSES = [("bad run (flag 4)", BAD), ("metadata to check", TO_CHECK), ("nothing to report", SERIES)]
BAD_RUN = ("Incomplete run", "Few images per trigger")  # the checks of step 2 that flag a bin 4
WORLD = Path(__file__).with_name("land-50m.json")  # Natural Earth 1:50m land (public domain), via world-atlas (ISC)


def map_status(df):
    """The worst problem of each bin, for the map: bad run (red), metadata to check (orange: sample_metadata_flag 3 or
    9, position jump, lat/lon out of range), nothing to report (blue)."""
    to_check = df.get("to check", pd.Series("", index=df.index)).fillna("")
    meta_flag = df.get("sample_metadata_flag", pd.Series("", index=df.index)).fillna("").astype(str)
    position = why(df, MAP) != "ok"
    return pd.Series(["bad run (flag 4)" if any(c in t for c in BAD_RUN) else
                      "metadata to check" if f in ("3", "9") or p else "nothing to report"
                      for t, f, p in zip(to_check, meta_flag, position)], index=df.index)


def position_map(df, width=900, height=460):
    """(Altair map of the bins with a position, or None; {status: number of bins, with 'no position'}). Land, a grid
    with the degrees on the edges, and one point per bin coloured by map_status. Mercator, framed on the bins. The
    positions come from the metadata of step 3: the IFCB records none."""
    import json
    import math

    import altair as alt
    if not {"object_lat", "object_lon"} <= set(df.columns):
        return None, {}
    d = df.assign(status=map_status(df))
    pos = d.dropna(subset=["object_lat", "object_lon"])
    counts = {s: int((pos["status"] == s).sum()) for s, _ in STATUSES if (pos["status"] == s).any()}
    if len(d) > len(pos):
        counts["no position"] = len(d) - len(pos)
    if pos.empty:
        return None, counts

    def merc(lat):
        return math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))

    def unmerc(y):
        return math.degrees(2 * math.atan(math.exp(y)) - math.pi / 2)

    lon0, lon1 = pos["object_lon"].min(), pos["object_lon"].max()
    lat0, lat1 = pos["object_lat"].min(), pos["object_lat"].max()
    pad = max(0.2, 0.08 * max(lon1 - lon0, lat1 - lat0))
    lon0, lon1, lat0, lat1 = lon0 - pad, lon1 + pad, max(lat0 - pad, -80), min(lat1 + pad, 80)
    scale = min(width / math.radians(lon1 - lon0), height / (merc(lat1) - merc(lat0)))
    clon, cy = (lon0 + lon1) / 2, (merc(lat0) + merc(lat1)) / 2
    # what the frame really shows, to put the degrees on its edges
    left, right = clon - math.degrees(width / 2 / scale), clon + math.degrees(width / 2 / scale)
    bottom, top = unmerc(cy - height / 2 / scale), unmerc(cy + height / 2 / scale)
    span = max(right - left, top - bottom)
    step = next(s for s in (0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 30) if span / s <= 8)
    projection = dict(type="mercator", scale=scale, center=[clon, unmerc(cy)],
                                clipExtent=[[0, 0], [width, height]])

    def ticks(a, b):
        margin = 0.04 * (b - a)  # a label on the very edge of the frame is cut
        return [round(k * step, 6) for k in range(math.ceil((a + margin) / step), math.floor((b - margin) / step) + 1)]

    def degrees(v, pos_, neg):
        return f"{abs(v):g}°{pos_ if v >= 0 else neg}"

    with open(WORLD, encoding="utf-8") as f:
        world = json.load(f)
    land = alt.Chart(alt.Data(values=world, format=alt.DataFormat(type="topojson", feature="land"))).mark_geoshape(
        fill="#e9ecef", stroke="#b8c2c8", strokeWidth=0.6).project(**projection)
    grid = alt.Chart(alt.graticule(step=[step, step])).mark_geoshape(
        filled=False, stroke="#c9d1d6", strokeWidth=0.5, strokeDash=[3, 3]).project(**projection)
    lon_labels = pd.DataFrame([{"lon": v, "lat": bottom, "label": degrees(v, "E", "W")} for v in ticks(left, right)])
    lat_labels = pd.DataFrame([{"lon": left, "lat": v, "label": degrees(v, "N", "S")} for v in ticks(bottom, top)])
    text = {"fontSize": 11, "color": "#5a6b74"}
    labels = [alt.Chart(lon_labels).mark_text(dy=-8, **text).encode(longitude="lon:Q", latitude="lat:Q", text="label:N")
              .project(**projection),
              alt.Chart(lat_labels).mark_text(dx=6, align="left", **text).encode(
                  longitude="lon:Q", latitude="lat:Q", text="label:N").project(**projection)]
    present = [(s, c) for s, c in STATUSES if s in counts]
    cols = [c for c in ("bin", "time", "object_lat", "object_lon", "status", "to check") if c in pos.columns]
    points = alt.Chart(pos[cols].assign(top=(pos["status"] != "nothing to report").astype(int))).mark_circle(
        size=45, opacity=0.85).encode(
        longitude="object_lon:Q", latitude="object_lat:Q",
        color=alt.Color("status:N", title=None, scale=alt.Scale(domain=[s for s, _ in present], range=[c for _, c in present]),
                        legend=alt.Legend(orient="top")),
        order=alt.Order("top:Q"),
        tooltip=["bin", alt.Tooltip("time:T", format="%Y-%m-%d %H:%M"), alt.Tooltip("object_lat:Q", title="latitude", format=".4f"),
                 alt.Tooltip("object_lon:Q", title="longitude", format=".4f"), "status", "to check"]).project(**projection)
    chart = alt.layer(land, grid, points, *labels).properties(width=width, height=height)
    return chart, counts


@functools.lru_cache(maxsize=1)
def coastline():
    """DataFrame lon, lat, ring, order: the outline of the land of WORLD (TopoJSON: quantized arcs, delta-encoded),
    to draw as lines in a longitude / latitude chart."""
    with open(WORLD, encoding="utf-8") as f:
        topo = json.load(f)
    (sx, sy), (tx, ty) = topo["transform"]["scale"], topo["transform"]["translate"]
    arcs = []
    for arc in topo["arcs"]:
        x = y = 0
        points = []
        for dx, dy in arc:
            x, y = x + dx, y + dy
            points.append((x * sx + tx, y * sy + ty))
        arcs.append(points)
    rows, ring_id = [], 0
    land = topo["objects"]["land"]
    geometries = land.get("geometries", [land])  # one geometry, or a collection of them
    for g in geometries:
        polygons = g["arcs"] if g["type"] == "MultiPolygon" else [g["arcs"]]
        for polygon in polygons:
            for ring in polygon:
                points = []
                for k in ring:  # a negative index is the arc ~k reversed
                    a = arcs[k] if k >= 0 else arcs[~k][::-1]
                    points.extend(a if not points else a[1:])
                rows.extend({"lon": x, "lat": y, "ring": ring_id, "order": n} for n, (x, y) in enumerate(points))
                ring_id += 1
    return pd.DataFrame(rows)


ISOBATHS = (200, 1000, 2000, 4000)  # metres, as on the maps of the cruise reports
BATHY_URL = ("https://coastwatch.pfeg.noaa.gov/erddap/griddap/etopo180.csv?"
             "altitude%5B({lat0}):{s}:({lat1})%5D%5B({lon0}):{s}:({lon1})%5D")  # NOAA ETOPO1, public domain, 1 arc-minute
BATHY_CACHE = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "ifcb_app" / "bathymetry"


@functools.lru_cache(maxsize=16)
def bathymetry(lon0, lon1, lat0, lat1):
    """DataFrame lon, lat, depth, line, order: the ISOBATHS of the box, from NOAA ETOPO1 (asked once per box, then
    kept in BATHY_CACHE). Empty when offline: the maps are drawn without them. Only the box is sent to NOAA."""
    lon0, lon1, lat0, lat1 = (math.floor(lon0), math.ceil(lon1), max(math.floor(lat0), -89), min(math.ceil(lat1), 89))
    cache = BATHY_CACHE / f"isobaths_{lon0}_{lon1}_{lat0}_{lat1}.csv"
    if cache.is_file():
        return pd.read_csv(cache)
    try:
        import io
        import urllib.request

        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        stride = max(1, math.ceil(max(lon1 - lon0, lat1 - lat0) * 60 / 300))  # at most ~300 points a side
        url = BATHY_URL.format(lat0=lat0, lat1=lat1, lon0=lon0, lon1=lon1, s=stride)
        with urllib.request.urlopen(url, timeout=30) as r:
            grid = pd.read_csv(io.StringIO(r.read().decode("utf-8")), skiprows=[1])
        z = grid.pivot(index="latitude", columns="longitude", values="altitude")
        fig, ax = plt.subplots()
        cs = ax.contour(z.columns.values, z.index.values, z.values, levels=sorted(-d for d in ISOBATHS))
        plt.close(fig)
        rows, line = [], 0
        for level, segments in zip(cs.levels, cs.allsegs):
            for seg in segments:
                rows.extend({"lon": x, "lat": y, "depth": int(-level), "line": line, "order": n} for n, (x, y) in enumerate(seg))
                line += 1
        out = pd.DataFrame(rows, columns=["lon", "lat", "depth", "line", "order"])
        BATHY_CACHE.mkdir(parents=True, exist_ok=True)
        out.to_csv(cache, index=False)
        return out
    except Exception:  # offline, server down: no isobaths
        return pd.DataFrame(columns=["lon", "lat", "depth", "line", "order"])


def track_axes(lon, lat):
    """(x, y, background layers) for a chart of the bins by longitude and latitude: the axes framed on the bins, the
    coastline and the isobaths behind the points (clipped to the frame)."""
    import altair as alt
    lon0, lon1, lat0, lat1 = lon.min(), lon.max(), lat.min(), lat.max()
    pad = max(0.1, 0.05 * max(lon1 - lon0, lat1 - lat0))
    lon0, lon1, lat0, lat1 = lon0 - pad, lon1 + pad, lat0 - pad, lat1 + pad
    x = alt.X("lon:Q", title="Longitude (degrees E)", scale=alt.Scale(domain=[lon0, lon1], zero=False, nice=False))
    y = alt.Y("lat:Q", title="Latitude (degrees N)", scale=alt.Scale(domain=[lat0, lat1], zero=False, nice=False))
    span = max(lon1 - lon0, lat1 - lat0)
    box = (lon0 - span / 4, lon1 + span / 4, lat0 - span / 4, lat1 + span / 4)  # a margin for zooming out a little
    coast = clip_lines(coastline(), "ring", box, 2500)
    layers = [alt.Chart(coast).mark_line(color="#5a6b74", strokeWidth=1.2, clip=True).encode(
        x=x, y=y, detail="piece:N", order="order:Q")]
    bathy = clip_lines(bathymetry(lon0 - span / 2, lon1 + span / 2, lat0 - span / 2, lat1 + span / 2), "line", box, 2000)
    if len(bathy):
        layers.insert(0, alt.Chart(bathy).mark_line(color="#6fa8c8", strokeWidth=0.9, clip=True).encode(
            x=x, y=y, detail="piece:N", order="order:Q",
            opacity=alt.Opacity("depth:O", legend=None, scale=alt.Scale(range=[0.95, 0.5])),
            tooltip=[alt.Tooltip("depth:Q", title="isobath (m)")]))
    return x, y, layers


def clip_lines(lines, line_id, box, most):
    """The points of `lines` (lon, lat, order, and a line id) inside box (lon0, lon1, lat0, lat1), with a new 'piece'
    wherever points were left out, so that no straight segment joins two pieces; thinned to about `most` points
    (Altair takes at most 5000 rows per chart)."""
    lon0, lon1, lat0, lat1 = box
    inside = lines["lon"].between(lon0, lon1) & lines["lat"].between(lat0, lat1)
    if not inside.any():
        return lines.iloc[0:0].assign(piece=[])
    breaks = (~inside).cumsum()  # rises at every point left out: the pieces on either side differ
    kept = lines.assign(piece=lines[line_id].astype(str) + "_" + breaks.astype(str))[inside]
    step = max(1, math.ceil(len(kept) / most))
    return kept.iloc[::step]


def flag_chart(df, height=260):
    """The bins by longitude and latitude, coloured by map_status (red bad run, orange metadata to check, blue nothing
    to report), the reasons on hover; zoomable. None without positions."""
    import altair as alt
    if not {"object_lat", "object_lon"} <= set(df.columns):
        return None
    d = df.assign(status=map_status(df)).dropna(subset=["object_lat", "object_lon"])
    if d.empty:
        return None
    present = [(s, c) for s, c in STATUSES if (d["status"] == s).any()]
    cols = [c for c in ("bin", "time", "object_lat", "object_lon", "status", "to check") if c in d.columns]
    d = d[cols].assign(lon=d["object_lon"], lat=d["object_lat"], top=(d["status"] != "nothing to report").astype(int))
    x, y, background = track_axes(d["lon"], d["lat"])
    points = alt.Chart(d).mark_circle(size=40, opacity=0.85, clip=True).encode(
        x=x, y=y,
        color=alt.Color("status:N", title=None, scale=alt.Scale(domain=[s for s, _ in present], range=[c for _, c in present]),
                        legend=alt.Legend(orient="top")),
        order=alt.Order("top:Q"),
        tooltip=["bin", alt.Tooltip("time:T", format="%Y-%m-%d %H:%M"), "object_lat", "object_lon", "status", "to check"])
    return zoomable(alt.layer(*background, points).properties(height=height))


MAP_FIGURE = "sample_qc_map.png"


def figure_name(column, against):
    """File name of a figure in figures/ (Amanda, 2026-11-24): sample_qc_… for the sampling metadata (columns sample_
    and object_ of step 3), acquisition_qc_… for the values of the IFCB itself (concentration, images per trigger,
    stream, cut images…). E.g. sample_qc_salinity_pss_time.png, acquisition_qc_rois_per_ml_time.png."""
    if column.startswith(("sample_", "object_")):
        return f"sample_qc_{column.removeprefix('sample_')}_{against}.png"
    return f"acquisition_qc_{column}_{against}.png"


def charts(df, variables, depth):
    """{file name in figures/: (title, Altair chart)}: the map, then each variable against time (and depth)."""
    import altair as alt
    tooltip = ["bin", alt.Tooltip("time:T", format="%Y-%m-%d %H:%M"), alt.Tooltip("rois_per_ml:Q", format=".0f"),
               "rois", alt.Tooltip("ml_analyzed:Q", format=".2f"), "to check"]

    base = ["bin", "time", "rois_per_ml", "rois", "ml_analyzed", "to check"]

    def data(keys, columns):
        """Only the columns the chart uses: with every column, 1400 bins made 0.8 MB per chart in the page."""
        w = why(df, *keys)
        cols = list(dict.fromkeys(base + [c for c in columns if c in df.columns]))
        return df[cols].assign(why=w, flagged=(w != "ok").astype(int))  # flagged points drawn on top

    def scatter(d, x, y, x_title, y_title, reverse_y=False):
        x_axis = (alt.X(x, title=x_title, axis=alt.Axis(format="%d %b %H:%M", labelAngle=-30), scale=alt.Scale(padding=12))
                  if x.endswith(":T") else alt.X(x, title=x_title, scale=alt.Scale(zero=False, padding=12)))
        extra = [c for c in (y.split(":")[0], x.split(":")[0]) if c not in ("time", "rois_per_ml")]
        return zoomable(alt.Chart(d).mark_circle(size=60, opacity=0.8).encode(
            x=x_axis, y=alt.Y(y, title=y_title, scale=alt.Scale(reverse=reverse_y, zero=False, padding=12)),
            color=colour(d["why"]), order=alt.Order("flagged:Q"), tooltip=tooltip + extra).properties(height=260))

    figures = {}
    chart, _ = position_map(df)
    if chart is not None:
        figures[MAP_FIGURE] = ("Sample positions", chart)
    depth_varies = bool(depth) and df[depth].nunique() > 1  # a constant depth (underway intake) shows nothing
    position = {"object_lon", "object_lat"} <= set(df.columns) and df["object_lon"].notna().any()
    for c, title, by_depth in variables:
        figures[figure_name(c, "time")] = (f"{title} against time",
                                       scatter(data([c], [c]), "time:T", f"{c}:Q", "Time (UTC)", title))
        if depth_varies and by_depth:
            figures[figure_name(c, "depth")] = (f"{title} against depth", scatter(
                data([c, depth], [c, depth]), f"{c}:Q", f"{depth}:Q", title, "Depth (m)", reverse_y=True))
        if position and by_depth:  # west -> east and south -> north
            figures[figure_name(c, "longitude")] = (f"{title} against longitude", scatter(
                data([c, MAP], [c, "object_lon"]), "object_lon:Q", f"{c}:Q", "Longitude (degrees E, west → east)", title))
            figures[figure_name(c, "latitude")] = (f"{title} against latitude", scatter(
                data([c, MAP], [c, "object_lat"]), "object_lat:Q", f"{c}:Q", "Latitude (degrees N, south → north)", title))
            # the three axes at once: longitude, latitude and the value as a colour
            d = data([c], [c, "object_lon", "object_lat"]).dropna(subset=["object_lon", "object_lat", c])
            d = d.assign(lon=d["object_lon"], lat=d["object_lat"]).sort_values(c)
            if d.empty:
                continue
            x, y, background = track_axes(d["lon"], d["lat"])  # coastline and isobaths behind the points
            points = alt.Chart(d).mark_circle(size=45, opacity=0.9, clip=True).encode(
                x=x, y=y,
                # title on two lines (name, then unit); a log scale for the concentration, very skewed
                color=alt.Color(f"{c}:Q", title=[t.strip(") ") for t in title.split(" (")],
                                scale=alt.Scale(scheme="viridis", type="log" if c == "rois_per_ml" else "linear"),
                                legend=alt.Legend(orient="right", gradientLength=200)),
                tooltip=tooltip + ["object_lat", "object_lon", c])
            figures[figure_name(c, "map")] = (f"{title} along the track",
                                              zoomable(alt.layer(*background, points).properties(height=260)))
    return figures


def zoomable(chart):
    """Pan by dragging, zoom with Shift + mouse wheel, double-click to reset. A plain mouse wheel scrolls the page:
    with .interactive() it zoomed the chart under the mouse, which then looked empty."""
    import altair as alt
    return chart.add_params(alt.selection_interval(bind="scales", zoom="wheel![event.shiftKey]"))


def roi_chart(rois, frame_right):
    """ROI x against ROI y of the images of one bin, as the RoiXY graph of IFCBacquire and the plot of the IFCB
    dashboard: the images should form one compact band; spread out, they point to a bad flow. Images touching the
    left or right edge of the camera field (cut images) are coloured."""
    import altair as alt
    df = pd.DataFrame(rois)
    df["where"] = [EDGE if r.x <= 0 or r.x + r.width >= frame_right else "inside" for r in df.itertuples()]
    return alt.Chart(df).mark_circle(size=14, opacity=0.5).encode(
        x=alt.X("x:Q", title="ROI x (pixels)", scale=alt.Scale(domain=[0, frame_right])),
        y=alt.Y("y:Q", title="ROI y (pixels)", scale=alt.Scale(zero=True)),
        color=alt.Color("where:N", scale=alt.Scale(domain=["inside", EDGE], range=[SERIES, TO_CHECK]),
                        legend=alt.Legend(title=None, orient="top", labelLimit=500)),
        order=alt.Order("where:N"),
        tooltip=["roi", "x", "y", "width", "height", "where"]).properties(height=320)


def save(folder, figures, df, force=False):
    """Save the figures as PNG in <folder>/figures/, unless the same data were already saved (force: always).
    Returns {"signature", "saved", "files"}. Needs vl-convert-python."""
    fig_dir = Path(folder) / "figures"
    signature = hashlib.sha1((df.to_csv() + ",".join(sorted(figures))).encode()).hexdigest()
    try:
        saved = json.loads((fig_dir / "qc_figures.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = {}
    if force or saved.get("signature") != signature:
        fig_dir.mkdir(exist_ok=True)
        for old in set(saved.get("files", [])) - set(figures):  # our own older figures, e.g. the qc_*.png names
            (fig_dir / old).unlink(missing_ok=True)
        for name, (title, chart) in figures.items():
            chart.properties(width=700, title=f"{Path(folder).name} · {title}").save(str(fig_dir / name), scale_factor=2)
        saved = {"signature": signature, "saved": datetime.now().isoformat(timespec="seconds"), "files": sorted(figures)}
        (fig_dir / "qc_figures.json").write_text(json.dumps(saved, indent=1), encoding="utf-8")
    return saved
