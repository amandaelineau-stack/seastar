import hashlib
import json
import os
import subprocess
import sys
import tempfile

import openpyxl

import make_bodc

A, B = "D20250810T164958_IFCB225", "D20250810T171346_IFCB225"


def test_make_bodc():
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "ifcb_sn225_t1_test")
        day = os.path.join(root, "raw", "D20250810")
        os.makedirs(day)
        os.makedirs(os.path.join(root, "metadata"))
        for b in (A, B):
            for ext in (".adc", ".hdr", ".roi"):
                with open(os.path.join(day, b + ext), "wb") as f:
                    f.write((b + ext).encode())
        with open(os.path.join(root, "metadata", "metadata_ecotaxa.csv"), "w", encoding="utf-8") as f:
            f.write(f"bin,object_lat,object_lon,sample_depth_min\n{A},48.97,-16.5,30\n")
        run = [sys.executable, make_bodc.__file__, root]
        subprocess.run(run, check=True, capture_output=True)

        ref = json.load(open(make_bodc.ref_path(root, A)))
        assert ref["files"][2] == {"relative_path": f"../../raw/D20250810/{A}.roi",
                                   "sha256": hashlib.sha256((A + ".roi").encode()).hexdigest()}
        wb = openpyxl.load_workbook(make_bodc.xlsx_path(root))
        rows = [r[1:8] for r in wb["Event items"].iter_rows(min_row=4, values_only=True)]
        assert rows[0][:5] == ("D20250810T164958", f"refs/{A}.json", 48.97, -16.5, -30), rows
        assert rows[0][5] == ref["set_uuid"] and rows[1][2] is None  # B: no position in step 4
        header = {r[0]: r[1] for r in wb["Event header"].iter_rows(min_row=2, values_only=True)}
        assert header["image-set-name"] == "ifcb_sn225_t1_test" and header["image-set-min-latitude-degrees"] == 48.97
        set_uuid = header["image-set-uuid"]

        assert make_bodc.plan(root) == ([], [A, B])  # up to date: not hashed again
        os.utime(os.path.join(day, B + ".hdr"), (2e9, 2e9))  # a raw file changed after its json
        assert make_bodc.plan(root)[0] == [B]
        subprocess.run(run, check=True, capture_output=True)
        assert json.load(open(make_bodc.ref_path(root, A)))["set_uuid"] == ref["set_uuid"]
        header = {r[0]: r[1] for r in openpyxl.load_workbook(make_bodc.xlsx_path(root))["Event header"]
                  .iter_rows(min_row=2, values_only=True)}
        assert header["image-set-uuid"] == set_uuid  # UUIDs kept when made again


if __name__ == "__main__":
    test_make_bodc()
    print("ok")
