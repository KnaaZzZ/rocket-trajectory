"""Download motor thrust curves from thrustcurve.org into data/library.

Fetches every RASP (.eng) motor for a manufacturer via the public
thrustcurve.org API and writes each as
``data/library/<ManufacturerAbbrev>_<designation>.eng``.

Examples:
    python download_motors.py                     # all Cesaroni motors
    python download_motors.py --manufacturer AeroTech
    python download_motors.py --impulse-class N   # only class N
    python download_motors.py --out data/motor_library
"""

import argparse
import base64
import json
import os
import re
import urllib.request

API = "https://www.thrustcurve.org/api/v1"
DOWNLOAD_BATCH = 25  # motorIds per download request


def _post(endpoint, payload):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{API}/{endpoint}", data=data,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)


def search_motors(manufacturer, impulse_class=None, max_results=2000):
    """Return the search result records for a manufacturer (optionally a class)."""
    payload = {"manufacturer": manufacturer, "maxResults": max_results}
    if impulse_class:
        payload["impulseClass"] = impulse_class
    return _post("search.json", payload).get("results", [])


def download_eng_files(motor_ids):
    """Return {motorId: eng_text} for the given ids (first RASP file per motor)."""
    out = {}
    for start in range(0, len(motor_ids), DOWNLOAD_BATCH):
        batch = motor_ids[start:start + DOWNLOAD_BATCH]
        payload = {"motorIds": batch, "format": "RASP", "data": "file"}
        for record in _post("download.json", payload).get("results", []):
            mid = record["motorId"]
            if mid not in out:  # keep the first RASP file if a motor has several
                out[mid] = base64.b64decode(record["data"]).decode("utf-8", "replace")
        print(f"  downloaded {min(start + DOWNLOAD_BATCH, len(motor_ids))}"
              f"/{len(motor_ids)}")
    return out


def _sanitize(name):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_")


def download_manufacturer(manufacturer="Cesaroni", impulse_class=None,
                          out_dir="data/library"):
    """Download all RASP motors for a manufacturer into ``out_dir``."""
    results = search_motors(manufacturer, impulse_class)
    if not results:
        print(f"No motors found for {manufacturer!r}"
              f"{f' class {impulse_class}' if impulse_class else ''}.")
        return

    id_to_designation = {r["motorId"]: r["designation"] for r in results}
    abbrev = _sanitize(results[0].get("manufacturerAbbrev", manufacturer))
    print(f"Found {len(id_to_designation)} {manufacturer} motors. Downloading...")

    eng_by_id = download_eng_files(list(id_to_designation))

    os.makedirs(out_dir, exist_ok=True)
    saved, no_rasp = 0, []
    for mid, designation in id_to_designation.items():
        text = eng_by_id.get(mid)
        if not text:
            no_rasp.append(designation)
            continue
        path = os.path.join(out_dir, f"{abbrev}_{_sanitize(designation)}.eng")
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        saved += 1

    print(f"\nSaved {saved} .eng files to {out_dir}/")
    if no_rasp:
        preview = ", ".join(no_rasp[:8])
        print(f"{len(no_rasp)} motor(s) had no RASP file and were skipped: "
              f"{preview}{'...' if len(no_rasp) > 8 else ''}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manufacturer", default="Cesaroni")
    parser.add_argument("--impulse-class", default=None,
                        help="Single impulse class letter, e.g. N")
    parser.add_argument("--out", default="data/library", help="Output directory")
    args = parser.parse_args()
    download_manufacturer(args.manufacturer, args.impulse_class, args.out)


if __name__ == "__main__":
    main()
