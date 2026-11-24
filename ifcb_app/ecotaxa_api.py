"""Read-only statistics of EcoTaxa projects through the EcoTaxa API (https://ecotaxa.obs-vlfr.fr/api/docs).
No UI code here: used by the EcoTaxa API page of the app. Nothing is ever written to EcoTaxa.

Each person logs in with their own EcoTaxa account: the password is only sent to EcoTaxa to get a token, never
saved. The token is kept on this computer in the file the R client ecotaxarapi uses (Documents/.R/ecotaxa/token),
so R and the app share it. EcoTaxa shows each account only the projects it has access to.
"""
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

API = "https://ecotaxa.obs-vlfr.fr/api"
# Where ecotaxarapi::save_api_token() writes it: "~/.R/ecotaxa/token", and R's ~ is Documents on Windows
TOKEN_FILE = os.path.join(os.path.expanduser("~"), "Documents" if os.name == "nt" else "", ".R", "ecotaxa", "token")
STATUS = {"nb_validated": "Validated", "nb_dubious": "Dubious", "nb_predicted": "Predicted",
          "nb_unclassified": "Not classified"}


# A project split in parts in EcoTaxa: "… 1/4", "… (2 of 4)", "… part 3", "… 1 sur 4", "… _p2", at the end of the title
PART_RE = re.compile(r"[\s_\-–(\[]*(?:(?:part|partie|p)\s*\.?\s*(\d+)(?:\s*(?:/|of|sur|on)\s*\d+)?|(\d+)\s*(?:/|of|sur|on)\s*\d+)[)\]]?\s*$",
                     re.IGNORECASE)


def split_title(title):
    """(title without its part number, part number or None): "IFCB PAP2025 2/4" -> ("IFCB PAP2025", 2)."""
    m = PART_RE.search(title)
    if not m or not title[:m.start()].strip():
        return title, None
    return title[:m.start()].strip(" _-–"), int(m.group(1) or m.group(2))


class NotLoggedIn(Exception):
    """No token, or EcoTaxa refused it (expired): log in again."""


def read_token():
    try:
        with open(TOKEN_FILE, encoding="utf-8") as f:
            return f.read().strip().strip('"') or None
    except OSError:
        return None


def _call(route, token=None, body=None, **params):
    query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    req = urllib.request.Request(f"{API}/{route}" + (f"?{query}" if query else ""),
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json",
                                          **({"Authorization": f"Bearer {token}"} if token else {})})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403) and route != "login":
            raise NotLoggedIn("EcoTaxa refused the token (expired?): log in again") from None
        raise


def login(username, password):
    """Ask EcoTaxa for a token and save it (not the password). Raises HTTPError 401/403 on a wrong password."""
    token = _call("login", body={"username": username, "password": password})
    os.makedirs(os.path.dirname(TOKEN_FILE), exist_ok=True)
    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        f.write(token)
    return token


def logout():
    try:
        os.remove(TOKEN_FILE)
    except OSError:
        pass


def me(token):
    return _call("users/me", token)


def projects(token, instrument="IFCB"):
    """The projects of this instrument the account can see, as dicts: projid, title, contact, managers, objects,
    pct_validated, and group = the title without its part number (split projects share it), part."""
    out = []
    for p in _call("projects/search", token, instrument_filter=instrument, window_size=10000):
        group, part = split_title(p["title"])
        out.append({"projid": p["projid"], "title": p["title"], "group": group, "part": part,
                    "contact": (p.get("contact") or {}).get("name", ""),
                    "managers": ", ".join(m.get("name", "") for m in p.get("managers") or []),
                    "objects": p.get("objcount") or 0, "pct_validated": round(p.get("pctvalidated") or 0, 1)})
    return out


def project_status(token, ids):
    """{projid: {nb_validated, nb_dubious, nb_predicted, nb_unclassified}}."""
    rows = _call("project_set/taxo_stats", token, ids=",".join(map(str, ids)))
    return {r["projid"]: {k: r.get(k) or 0 for k in STATUS} for r in rows}


def taxa_counts(token, ids):
    """One row per project and taxon with objects: projid, taxon id, the counts of STATUS."""
    rows = _call("project_set/taxo_stats", token, ids=",".join(map(str, ids)), taxa_ids="all")
    out = []
    for r in rows:
        for taxon in r.get("used_taxa") or [None]:
            if taxon is not None and taxon != -1:
                out.append({"projid": r["projid"], "taxon": taxon, **{k: r.get(k) or 0 for k in STATUS}})
    return out


def taxa(token, ids):
    """{taxon id: {name, display_name, lineage, group}}; group = root of the lineage ('living', 'not-living'...)."""
    out = {}
    ids = sorted(set(ids))
    for i in range(0, len(ids), 200):  # the ids go in the URL
        for t in _call("taxon_set/query", token, ids=",".join(map(str, ids[i:i + 200]))):
            lineage = t.get("lineage") or []
            root = lineage[-1] if lineage else ""
            # "Biota" = root of the WoRMS taxa, "living" = root of the older EcoTaxa tree: both are living organisms
            out[t["id"]] = {"name": t["name"], "display_name": t.get("display_name") or t["name"],
                            "lineage": " > ".join(reversed(lineage)), "group": "living" if root == "Biota" else root}
    return out


def samples(token, projid):
    """The samples of a project: sampleid, orig_id (the bin name for IFCB), and their free columns."""
    return _call("samples/search", token, project_ids=projid, id_pattern="*")


def sample_status(token, sample_ids):
    """{sampleid: counts of STATUS}."""
    out = {}
    sample_ids = list(sample_ids)
    for i in range(0, len(sample_ids), 200):
        for r in _call("sample_set/taxo_stats", token, sample_ids=",".join(map(str, sample_ids[i:i + 200]))):
            out[r["sample_id"]] = {k: r.get(k) or 0 for k in STATUS}
    return out
