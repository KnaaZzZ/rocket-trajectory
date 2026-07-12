"""Persistence for the GUI: remembered inputs and a saved-results history.

- Settings (form values + chosen motors) live in a single JSON file so the app
  reopens with whatever the user last had.
- Each optimizer run is saved under RESULTS_DIR so past runs can be reopened.
"""

import json
import os
import time

SETTINGS_FILE = "gui_state.json"
PRESETS_FILE = "presets.json"
MOTOR_PRESETS_FILE = "motor_presets.json"
CONFIGS_FILE = "saved_configs.json"
RESULTS_DIR = "data/results"


# --- settings -----------------------------------------------------------
def load_settings():
    """Return the saved settings dict, or {} if none / unreadable."""
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_settings(settings):
    """Persist the settings dict (best effort; errors are ignored)."""
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)
    except OSError:
        pass


# --- presets (per input group) -----------------------------------------
def load_presets():
    """Return {group_key: {preset_name: {field_key: value}}}, or {} if none."""
    try:
        with open(PRESETS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_presets(presets):
    """Persist the presets dict (best effort)."""
    try:
        with open(PRESETS_FILE, "w", encoding="utf-8") as f:
            json.dump(presets, f, indent=2)
    except OSError:
        pass


def load_motor_presets():
    """Return {preset_name: [motor_name, ...]}, or {} if none."""
    try:
        with open(MOTOR_PRESETS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_motor_presets(presets):
    """Persist the motor-presets dict (best effort)."""
    try:
        with open(MOTOR_PRESETS_FILE, "w", encoding="utf-8") as f:
            json.dump(presets, f, indent=2)
    except OSError:
        pass


def load_saved_configs():
    """Return {name: {motor_file, mass, metrics, config}}, or {} if none."""
    try:
        with open(CONFIGS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_saved_configs(configs):
    """Persist the saved-configurations dict (best effort)."""
    try:
        with open(CONFIGS_FILE, "w", encoding="utf-8") as f:
            json.dump(configs, f, indent=2)
    except OSError:
        pass


# --- results history ----------------------------------------------------
def _serializable_results(results):
    """Keep only the JSON-friendly fields of each optimizer result."""
    slim = []
    for r in results:
        slim.append({
            "motor_file": r["motor_file"],
            "mass": r["mass"],
            "score": r["score"],
            "metrics": r["metrics"],
            "converged": r.get("converged", True),
        })
    return slim


def save_results(config, results, objective):
    """Write one run to RESULTS_DIR; return its file path."""
    os.makedirs(RESULTS_DIR, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    payload = {
        "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "objective": objective,
        "config": config,               # tuples become JSON arrays
        "results": _serializable_results(results),
    }
    path = os.path.join(RESULTS_DIR, f"{stamp}_{objective}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    return path


def list_results():
    """Return saved runs, newest first: list of {path, label, saved_at}."""
    if not os.path.isdir(RESULTS_DIR):
        return []
    entries = []
    for name in os.listdir(RESULTS_DIR):
        if not name.endswith(".json"):
            continue
        path = os.path.join(RESULTS_DIR, name)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        label = (f"{data.get('saved_at', name)}  |  "
                 f"{data.get('objective', '?')}  |  "
                 f"{len(data.get('results', []))} motors")
        entries.append({"path": path, "label": label,
                        "saved_at": data.get("saved_at", "")})
    entries.sort(key=lambda e: e["path"], reverse=True)  # filename is timestamped
    return entries


def load_results(path):
    """Load a saved run; return the payload dict (config, objective, results)."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
