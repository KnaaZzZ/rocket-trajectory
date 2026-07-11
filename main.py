"""Entry point: rank configurations, then let the user pick one for full output.

1. Optimize airframe mass per motor and print the ranked table.
2. Prompt the user to choose a configuration by rank.
3. Re-run that configuration and emit all data and curves (+ a CSV export).
"""

import os
import sys

# RocketPy's reports print Unicode (e.g. phi/theta/psi); force UTF-8 so the
# Windows console (cp1252 by default) doesn't crash on them.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from config import CONFIG
from optimizer import _motor_name, optimize, report
from simulation import LIBRARY_DIR, SAVED_DIR, find_motor_files, full_details

OUTPUT_DIR = "output"


def cli_motor_files():
    """Motors for the CLI sweep: the saved set if any, else the full library.

    (The GUI lets you pick motors explicitly; the CLI defaults to your saved
    working set so `python main.py` doesn't sweep all 256 library motors.)
    """
    saved = find_motor_files(SAVED_DIR)
    if saved:
        print(f"Using {len(saved)} saved motor(s) from {SAVED_DIR}/.")
        return saved
    library = find_motor_files(LIBRARY_DIR)
    print(f"No saved motors; sweeping the full library "
          f"({len(library)} motors) -- this may take a while.")
    return library


def select_config(results):
    """Ask the user to pick a configuration by rank; return it or None."""
    try:
        raw = input(
            f"\nSelect a configuration for full output [1-{len(results)}] "
            "(blank to skip): "
        ).strip()
    except EOFError:
        return None  # non-interactive session
    if not raw:
        return None
    if not raw.isdigit() or not (1 <= int(raw) <= len(results)):
        print("Invalid selection; skipping full output.")
        return None
    return results[int(raw) - 1]


if __name__ == "__main__":
    results = optimize(CONFIG, motor_files=cli_motor_files())
    report(results, CONFIG["optimizer"]["objective"])

    selected = select_config(results)
    if selected:
        name = _motor_name(selected["motor_file"])
        export_path = os.path.join(OUTPUT_DIR, f"{name}_{selected['mass']:.2f}kg.csv")
        print(f"\nRunning full simulation: {name} @ {selected['mass']:.2f} kg\n")
        full_details(CONFIG, selected["motor_file"], selected["mass"], export_path)
