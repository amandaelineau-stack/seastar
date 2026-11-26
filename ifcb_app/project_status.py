"""Where one or several projects stand (step 1 and the Current status page of the app), and what the app would warn
about. Writes nothing.

    python project_status.py <project folder> [<project folder> ...]

Per day: bins in raw/, complete bins (hdr + adc + roi), bad bins (flag 4), feature files and EcoTaxa zips; days with
incomplete bins or good bins without a feature file or zip are marked with < (bad bins have none on purpose). Then the date of each step and the steps done out of order (e.g. EcoTaxa zips older than the
metadata), and the warnings: incomplete bins, bins without features, no metadata file, points still to check,
last runs that failed or stopped, WHOI version. Run it before each step.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import project


def show(root):
    s = project.scan_project(root)
    print(f"Project: {root}")
    if not s["name_info"]:
        print("  The folder name does not follow ifcb_sn<serial>_<cruise ID>_<name>.")
    missing = [d for d, ok in s["present"].items() if not ok]
    if missing:
        print("  Missing folders: " + ", ".join(missing))
    print("  Metadata files: " + (", ".join(s["metadata"]) or "none yet"))

    keys = ("bins", "complete_bins", "bad_bins", "features", "ecotaxa_zips")
    print(f"\n{'Day':10s} {'Bins':>6s} {'Complete':>9s} {'Bad':>5s} {'Features':>9s} {'Zips':>6s}")
    total = dict.fromkeys(keys, 0)
    for day, d in s["days"].items():
        print(f"{day:10s} {d['bins']:6d} {d['complete_bins']:9d} {d['bad_bins']:5d} {d['features']:9d} {d['ecotaxa_zips']:6d}"
              + ("  <" if project.day_incomplete(d) else ""))
        for k in keys:
            total[k] += d[k]
    print(f"{'Total':10s} {total['bins']:6d} {total['complete_bins']:9d} {total['bad_bins']:5d} {total['features']:9d} {total['ecotaxa_zips']:6d}")

    t = project.timeline(root)
    print("\nSteps:")
    print(f"  Raw data check: {t['raw_check'] or 'not done'}" + (f", {t['bad_bins']} bad bin(s)" if t["bad_bins"] else ""))
    bad = lambda n: f" (bad bins: {n} / {t['bad_bins']}, not processed unless --include-bad)" if t["bad_bins"] else ""
    print(f"  Features:       {t['features']} / {t['good_bins']} bins" + bad(t["bad_features"])
          + (f", made {t['features_made']}" if t["features_made"] else ""))
    print("  Metadata:       " + (f"saved {t['metadata']}, {t['metadata_flagged']} bin(s) flagged"
                                  + (f"; last check: {t['metadata_check']}" if t["metadata_check"] else "")
                                  if t["metadata"] else "not saved"))
    print(f"  EcoTaxa tables: {t['zips']} / {t['good_bins']} bins" + bad(t["bad_zips"])
          + (f", made {t['zips_made']}" if t["zips_made"] else ""))
    print("Order of the steps: " + ("ok" if not t["order"] else f"{len(t['order'])} point(s)"))
    for line in t["order"]:
        print(f"  - {line}")

    warnings = project.warnings(root, "all")
    print(f"\n{len(warnings)} warning(s)" + (":" if warnings else "."))
    for w in warnings:
        print(f"  - {w}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("projects", nargs="+", metavar="project")
    for i, p in enumerate(parser.parse_args().projects):
        root = os.path.abspath(p)
        if i:
            print("\n" + "=" * 80 + "\n")
        if os.path.isdir(root):
            show(root)
        else:
            print(f"No folder {root}")


if __name__ == "__main__":
    main()
