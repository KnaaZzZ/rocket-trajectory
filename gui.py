"""Tkinter GUI for the point-mass trajectory optimizer.

Columns, left to right:
  * Config  -- edit the environment / rocket / flight / optimizer parameters.
  * Motors  -- pick which motors to optimize: filter the library, move motors
               into the "Chosen" list, add new motors (paste or file), and save
               the chosen set to data/saved.
  * Results -- the ranked table of configurations. Double-click (or select +
               "Show data & plots") a row to run that configuration in full: its
               numeric summary and every flight plot open in a tabbed window,
               and the time series is exported to CSV.

Run with:  python gui.py
"""

import os
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

import matplotlib

matplotlib.use("Agg")  # figures are embedded via FigureCanvasTkAgg, not shown
from matplotlib.backends.backend_tkagg import (  # noqa: E402
    FigureCanvasTkAgg,
    NavigationToolbar2Tk,
)

import store  # noqa: E402
from optimizer import optimize  # noqa: E402
from simulation import (  # noqa: E402
    LIBRARY_DIR,
    SAVED_DIR,
    find_motor_files,
    generate_flight_figures,
    metrics,
    motor_name,
    run,
    save_motor_files,
    save_motor_text,
    validate_eng_text,
)

OUTPUT_DIR = "output"

OBJECTIVES = [
    "apogee", "apogee_time", "max_speed", "max_mach", "max_acceleration",
    "apogee_capped_mach", "min_mass_for_altitude",
]

# Form fields: (config path tuple, label, kind). kind: "float" | "int" | "combo".
FIELDS = [
    ("Environment", [
        (("environment", "latitude"), "Latitude (deg)", "float"),
        (("environment", "longitude"), "Longitude (deg)", "float"),
        (("environment", "elevation"), "Elevation (m ASL)", "float"),
    ]),
    ("Rocket (constant C_d*A)", [
        (("rocket", "drag"), "Drag coefficient C_d", "float"),
        (("rocket", "reference_area"), "Reference area (m^2)", "float"),
    ]),
    ("Flight", [
        (("flight", "rail_length"), "Rail length (m)", "float"),
        (("flight", "inclination"), "Inclination (deg from horiz.)", "float"),
        (("flight", "heading"), "Heading (deg, 0=N)", "float"),
    ]),
    ("Optimizer", [
        (("optimizer", "objective"), "Objective", "combo"),
        (("optimizer", "mach_limit"), "Mach limit (capped_mach)", "float"),
        (("optimizer", "target_altitude"), "Target altitude (min_mass)", "float"),
        (("optimizer", "mass_initial"), "Initial mass guess (kg)", "float"),
        (("optimizer", "mass_min"), "Mass min (kg)", "float"),
        (("optimizer", "mass_max"), "Mass max (kg)", "float"),
        (("optimizer", "fd_step"), "FD step (kg)", "float"),
        (("optimizer", "max_step"), "Max step (kg)", "float"),
        (("optimizer", "tol"), "Tolerance (kg)", "float"),
        (("optimizer", "max_iter"), "Max iterations", "int"),
    ]),
]

TABLE_COLUMNS = [
    ("rank", "#", 36),
    ("motor", "Motor", 170),
    ("mass", "Mass (kg)", 75),
    ("apogee", "Apogee (m)", 90),
    ("apogee_time", "Apo t (s)", 70),
    ("max_speed", "MaxV (m/s)", 80),
    ("max_mach", "Max Mach", 75),
    ("max_acceleration", "MaxAcc (m/s^2)", 100),
]


class OptimizerGUI:
    def __init__(self, root):
        self.root = root
        root.title("Point-Mass Trajectory Optimizer")
        root.geometry("1380x720")

        self.vars = {}          # config path -> tk StringVar
        self.results = []       # last optimize() results, index-aligned to rows
        self.all_motors = {}    # motor name -> .eng path (library + saved + added)
        self.chosen = {}        # motor name -> .eng path (optimizer runs these)
        self.cfg = None         # config used for the currently shown results
        self.preset_combos = {}  # group key -> ttk.Combobox of preset names
        self._busy = False

        self.presets = store.load_presets()
        self.settings = store.load_settings()
        self._build_config_panel()
        self._build_motor_panel()
        self._build_results_panel()
        self._apply_settings(self.settings)   # blank unless previously saved
        self._load_library()
        self._apply_chosen(self.settings.get("chosen", []))
        self._refresh_recent()
        self._maybe_open_latest()

        root.protocol("WM_DELETE_WINDOW", self._on_close)

    # --- config panel ---------------------------------------------------
    def _build_config_panel(self):
        left = ttk.Frame(self.root, padding=8)
        left.pack(side=tk.LEFT, fill=tk.Y)

        for group_name, fields in FIELDS:
            box = ttk.LabelFrame(left, text=group_name, padding=6)
            box.pack(fill=tk.X, pady=3)
            self._build_preset_row(box, fields[0][0][0])  # group key = section
            for path, label, kind in fields:
                row = ttk.Frame(box)
                row.pack(fill=tk.X, pady=1)
                ttk.Label(row, text=label, width=25).pack(side=tk.LEFT)
                var = tk.StringVar()
                self.vars[path] = var
                if kind == "combo":
                    widget = ttk.Combobox(row, textvariable=var, values=OBJECTIVES,
                                          state="readonly", width=15)
                else:
                    widget = ttk.Entry(row, textvariable=var, width=17)
                widget.pack(side=tk.RIGHT)

    def _build_preset_row(self, box, group_key):
        """Preset combobox + Save/Load/Delete for one input group."""
        row = ttk.Frame(box)
        row.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(row, text="Preset:").pack(side=tk.LEFT)
        combo = ttk.Combobox(row, state="readonly", width=10)
        combo.pack(side=tk.LEFT, padx=2)
        self.preset_combos[group_key] = combo
        ttk.Button(row, text="Save", width=5,
                   command=lambda: self._save_preset(group_key)).pack(side=tk.LEFT)
        ttk.Button(row, text="Load", width=5,
                   command=lambda: self._load_preset(group_key)).pack(side=tk.LEFT)
        ttk.Button(row, text="Del", width=4,
                   command=lambda: self._delete_preset(group_key)).pack(side=tk.LEFT)
        self._refresh_preset_combo(group_key)

    # --- motor panel ----------------------------------------------------
    def _build_motor_panel(self):
        mid = ttk.Frame(self.root, padding=8)
        mid.pack(side=tk.LEFT, fill=tk.Y)

        ttk.Label(mid, text="Motor library", font=("", 10, "bold")).pack(anchor=tk.W)
        filt = ttk.Frame(mid)
        filt.pack(fill=tk.X, pady=2)
        ttk.Label(filt, text="Filter:").pack(side=tk.LEFT)
        self.filter_var = tk.StringVar()
        self.filter_var.trace_add("write", lambda *a: self._refresh_available())
        ttk.Entry(filt, textvariable=self.filter_var).pack(
            side=tk.LEFT, fill=tk.X, expand=True)

        avail_box = ttk.Frame(mid)
        avail_box.pack(fill=tk.BOTH, expand=True)
        self.available_list = tk.Listbox(avail_box, selectmode=tk.EXTENDED, width=30,
                                         height=12, exportselection=False)
        asb = ttk.Scrollbar(avail_box, command=self.available_list.yview)
        self.available_list.config(yscrollcommand=asb.set)
        self.available_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        asb.pack(side=tk.RIGHT, fill=tk.Y)
        self.available_list.bind("<Double-1>", lambda e: self._add_selected())

        btns = ttk.Frame(mid)
        btns.pack(fill=tk.X, pady=3)
        ttk.Button(btns, text="Add →", command=self._add_selected).pack(
            side=tk.LEFT, expand=True, fill=tk.X)
        ttk.Button(btns, text="Add all shown", command=self._add_all_shown).pack(
            side=tk.LEFT, expand=True, fill=tk.X)
        ttk.Button(btns, text="← Remove", command=self._remove_selected).pack(
            side=tk.LEFT, expand=True, fill=tk.X)

        self.chosen_label = ttk.Label(mid, text="Chosen (optimizer runs these): 0")
        self.chosen_label.pack(anchor=tk.W)
        chosen_box = ttk.Frame(mid)
        chosen_box.pack(fill=tk.BOTH, expand=True)
        self.chosen_list = tk.Listbox(chosen_box, selectmode=tk.EXTENDED, width=30,
                                      height=9, exportselection=False)
        csb = ttk.Scrollbar(chosen_box, command=self.chosen_list.yview)
        self.chosen_list.config(yscrollcommand=csb.set)
        self.chosen_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        csb.pack(side=tk.RIGHT, fill=tk.Y)
        self.chosen_list.bind("<Double-1>", lambda e: self._remove_selected())

        actions = ttk.Frame(mid)
        actions.pack(fill=tk.X, pady=(3, 0))
        ttk.Button(actions, text="Add new motor…",
                   command=self._add_new_motor).pack(side=tk.LEFT, expand=True, fill=tk.X)
        ttk.Button(actions, text="Save chosen to saved/",
                   command=self._save_chosen).pack(side=tk.LEFT, expand=True, fill=tk.X)

    # --- results panel --------------------------------------------------
    def _build_results_panel(self):
        right = ttk.Frame(self.root, padding=8)
        right.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

        top = ttk.Frame(right)
        top.pack(fill=tk.X)
        self.run_btn = ttk.Button(top, text="Run Optimizer", command=self.on_run)
        self.run_btn.pack(side=tk.LEFT)
        ttk.Button(top, text="Clear inputs", command=self.on_clear).pack(
            side=tk.LEFT, padx=(6, 0))
        self.status = ttk.Label(top, text="Ready", foreground="gray")
        self.status.pack(side=tk.LEFT, padx=8)
        self.progress = ttk.Progressbar(right, mode="determinate")
        self.progress.pack(fill=tk.X, pady=4)

        recent = ttk.Frame(right)
        recent.pack(fill=tk.X)
        ttk.Label(recent, text="Recent runs:").pack(side=tk.LEFT)
        self.recent_var = tk.StringVar()
        self.recent_combo = ttk.Combobox(recent, textvariable=self.recent_var,
                                         state="readonly")
        self.recent_combo.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4)
        ttk.Button(recent, text="Open", command=self.on_open_recent).pack(side=tk.LEFT)

        ttk.Label(right, text="Configurations (ranked by objective)",
                  font=("", 11, "bold")).pack(anchor=tk.W)
        cols = [c[0] for c in TABLE_COLUMNS]
        self.tree = ttk.Treeview(right, columns=cols, show="headings", height=20)
        for key, heading, width in TABLE_COLUMNS:
            self.tree.heading(key, text=heading)
            self.tree.column(key, width=width, anchor=tk.W if key == "motor" else tk.E)
        self.tree.pack(fill=tk.BOTH, expand=True, pady=6)
        self.tree.bind("<Double-1>", lambda e: self.on_show_details())

        self.details_btn = ttk.Button(right, text="Show data & plots for selected",
                                      command=self.on_show_details, state=tk.DISABLED)
        self.details_btn.pack(anchor=tk.E)

    # --- motor list logic -----------------------------------------------
    def _load_library(self):
        self.all_motors = {}
        for directory in (LIBRARY_DIR, SAVED_DIR):
            for path in find_motor_files(directory):
                self.all_motors.setdefault(motor_name(path), path)
        self._refresh_available()

    def _refresh_available(self):
        needle = self.filter_var.get().strip().lower()
        self._available_names = sorted(
            n for n in self.all_motors if needle in n.lower()
        )
        self.available_list.delete(0, tk.END)
        for name in self._available_names:
            self.available_list.insert(tk.END, name)

    def _add_names(self, names):
        for name in names:
            self.chosen.setdefault(name, self.all_motors[name])
        self._refresh_chosen()

    def _add_selected(self):
        self._add_names(self._available_names[i]
                        for i in self.available_list.curselection())

    def _add_all_shown(self):
        self._add_names(list(self._available_names))

    def _remove_selected(self):
        names = [self._chosen_names[i] for i in self.chosen_list.curselection()]
        for name in names:
            self.chosen.pop(name, None)
        self._refresh_chosen()

    def _refresh_chosen(self):
        self._chosen_names = sorted(self.chosen)
        self.chosen_list.delete(0, tk.END)
        for name in self._chosen_names:
            self.chosen_list.insert(tk.END, name)
        self.chosen_label.config(
            text=f"Chosen (optimizer runs these): {len(self.chosen)}")

    def _save_chosen(self):
        if not self.chosen:
            messagebox.showinfo("Nothing to save", "Choose some motors first.")
            return
        paths = save_motor_files(list(self.chosen.values()), SAVED_DIR)
        messagebox.showinfo("Saved",
                            f"Saved {len(paths)} motor(s) to {SAVED_DIR}/.")

    def _add_new_motor(self):
        AddMotorDialog(self.root, self._on_new_motor)

    def _on_new_motor(self, name, path):
        """Called by the dialog once a new motor is validated and on disk."""
        self.all_motors[name] = path
        self.chosen[name] = path
        self._refresh_available()
        self._refresh_chosen()

    # --- config <-> form ------------------------------------------------
    @staticmethod
    def _path_key(path):
        return ".".join(path)

    def _apply_settings(self, settings):
        """Fill the form from saved settings; blank for anything not saved."""
        fields = settings.get("fields", {})
        for path, var in self.vars.items():
            var.set(fields.get(self._path_key(path), ""))

    def _apply_config(self, config):
        """Fill the form fields from a config dict (defaults or a saved run)."""
        for path, var in self.vars.items():
            section, key = path
            try:
                if key == "mass_min":
                    value = config["optimizer"]["mass_bounds"][0]
                elif key == "mass_max":
                    value = config["optimizer"]["mass_bounds"][1]
                else:
                    value = config[section][key]
            except (KeyError, IndexError, TypeError):
                continue  # leave the field as-is if the config lacks it
            var.set(str(value))

    def _collect_settings(self):
        return {
            "fields": {self._path_key(p): v.get() for p, v in self.vars.items()},
            "chosen": sorted(self.chosen),
        }

    def _apply_chosen(self, names):
        for name in names:
            if name in self.all_motors:
                self.chosen[name] = self.all_motors[name]
        self._refresh_chosen()

    def on_clear(self):
        for var in self.vars.values():
            var.set("")

    # --- presets (per input group) --------------------------------------
    def _group_paths(self, group_key):
        return [p for p in self.vars if p[0] == group_key]

    def _refresh_preset_combo(self, group_key):
        names = sorted(self.presets.get(group_key, {}))
        combo = self.preset_combos[group_key]
        combo["values"] = names
        if combo.get() not in names:
            combo.set("")

    def _save_preset(self, group_key):
        name = simpledialog.askstring(
            "Save preset", f"Name for this {group_key} preset:", parent=self.root)
        if not name:
            return
        name = name.strip()
        if not name:
            return
        values = {self._path_key(p): self.vars[p].get()
                  for p in self._group_paths(group_key)}
        self.presets.setdefault(group_key, {})[name] = values
        store.save_presets(self.presets)
        self._refresh_preset_combo(group_key)
        self.preset_combos[group_key].set(name)

    def _load_preset(self, group_key):
        name = self.preset_combos[group_key].get()
        preset = self.presets.get(group_key, {}).get(name)
        if not preset:
            messagebox.showinfo("No preset", "Pick a saved preset to load.")
            return
        for p in self._group_paths(group_key):
            if self._path_key(p) in preset:
                self.vars[p].set(preset[self._path_key(p)])

    def _delete_preset(self, group_key):
        name = self.preset_combos[group_key].get()
        if not name or name not in self.presets.get(group_key, {}):
            return
        if not messagebox.askyesno("Delete preset", f"Delete {group_key} preset "
                                   f"'{name}'?"):
            return
        del self.presets[group_key][name]
        store.save_presets(self.presets)
        self._refresh_preset_combo(group_key)

    def _on_close(self):
        store.save_settings(self._collect_settings())
        self.root.destroy()

    def _kind_for(self, path):
        for _, fields in FIELDS:
            for p, _, kind in fields:
                if p == path:
                    return kind
        return "float"

    # Per-field validation: label + a predicate the numeric value must satisfy.
    _RULES = {
        ("rocket", "drag"): ("Drag coefficient", lambda v: v >= 0, "must be >= 0"),
        ("rocket", "reference_area"): ("Reference area", lambda v: v > 0, "must be > 0"),
        ("flight", "rail_length"): ("Rail length", lambda v: v > 0, "must be > 0"),
        ("flight", "inclination"): ("Inclination", lambda v: 0 < v <= 90,
                                    "must be within (0, 90]"),
        ("optimizer", "mass_initial"): ("Initial mass", lambda v: v > 0, "must be > 0"),
        ("optimizer", "mass_min"): ("Mass min", lambda v: v > 0, "must be > 0"),
        ("optimizer", "mass_max"): ("Mass max", lambda v: v > 0, "must be > 0"),
        ("optimizer", "fd_step"): ("FD step", lambda v: v > 0, "must be > 0"),
        ("optimizer", "max_step"): ("Max step", lambda v: v > 0, "must be > 0"),
        ("optimizer", "tol"): ("Tolerance", lambda v: v > 0, "must be > 0"),
        ("optimizer", "max_iter"): ("Max iterations", lambda v: v >= 1, "must be >= 1"),
        ("optimizer", "mach_limit"): ("Mach limit", lambda v: v > 0, "must be > 0"),
        ("optimizer", "target_altitude"): ("Target altitude", lambda v: v > 0,
                                           "must be > 0"),
    }

    def _read_config(self):
        """Build a validated config from the form; raise ValueError listing issues."""
        cfg = {"environment": {}, "rocket": {}, "flight": {}, "optimizer": {}}
        errors = []
        values = {}
        objective = self.vars[("optimizer", "objective")].get().strip()
        # Objective-specific fields are only required for their objective.
        skip = set()
        if objective != "apogee_capped_mach":
            skip.add(("optimizer", "mach_limit"))
        if objective != "min_mass_for_altitude":
            skip.add(("optimizer", "target_altitude"))

        for path, var in self.vars.items():
            kind = self._kind_for(path)
            raw = var.get().strip()
            label = self._RULES.get(path, (path[-1],))[0] if path in self._RULES \
                else path[-1].replace("_", " ")
            if kind == "combo":
                if not raw:
                    errors.append("Objective: select one")
                elif raw not in OBJECTIVES:
                    errors.append(f"Objective: unknown value {raw!r}")
                values[path] = raw
                continue
            if path in skip:
                continue
            if raw == "":
                errors.append(f"{label}: required")
                continue
            try:
                value = int(float(raw)) if kind == "int" else float(raw)
            except ValueError:
                errors.append(f"{label}: must be a number")
                continue
            rule = self._RULES.get(path)
            if rule and not rule[1](value):
                errors.append(f"{rule[0]}: {rule[2]}")
            values[path] = value

        # Cross-field checks (only if both bounds parsed).
        lo = values.get(("optimizer", "mass_min"))
        hi = values.get(("optimizer", "mass_max"))
        if lo is not None and hi is not None and lo >= hi:
            errors.append("Mass min must be less than mass max.")

        if errors:
            raise ValueError("Please fix:\n  - " + "\n  - ".join(errors))

        # Build the config structure from the validated values.
        for path, value in values.items():
            section, key = path
            if key in ("mass_min", "mass_max"):
                continue  # folded into mass_bounds below
            cfg[section][key] = value
        cfg["optimizer"]["mass_bounds"] = (
            values[("optimizer", "mass_min")], values[("optimizer", "mass_max")])
        return cfg

    # --- run optimizer --------------------------------------------------
    def on_run(self):
        if self._busy:
            return
        if not self.chosen:
            messagebox.showinfo("No motors",
                                "Add at least one motor to the Chosen list.")
            return
        try:
            cfg = self._read_config()
        except ValueError as exc:
            messagebox.showerror("Invalid input", str(exc))
            return
        motor_files = list(self.chosen.values())
        self.progress.config(maximum=len(motor_files), value=0)
        self._set_busy(True, f"Optimizing {len(motor_files)} motor(s)...")
        threading.Thread(target=self._run_worker, args=(cfg, motor_files),
                         daemon=True).start()

    def _run_worker(self, cfg, motor_files):
        def progress(done, total, name):
            self.root.after(0, self._update_progress, done, total, name)
        try:
            results = optimize(cfg, motor_files=motor_files, progress=progress)
        except Exception as exc:
            self.root.after(0, self._run_failed, exc)
            return
        self.root.after(0, self._run_done, cfg, results)

    def _update_progress(self, done, total, name):
        self.progress.config(value=done)
        self.status.config(text=f"Optimizing {done}/{total}: {name}")

    def _populate_table(self, results):
        self.tree.delete(*self.tree.get_children())
        for i, r in enumerate(results, 1):
            m = r["metrics"]
            self.tree.insert("", tk.END, iid=str(i - 1), values=(
                i, motor_name(r["motor_file"]), f"{r['mass']:.2f}",
                f"{m['apogee']:.1f}", f"{m['apogee_time']:.2f}",
                f"{m['max_speed']:.1f}", f"{m['max_mach']:.2f}",
                f"{m['max_acceleration']:.1f}",
            ))

    def _run_done(self, cfg, results):
        self.cfg = cfg
        self.results = results
        objective = cfg["optimizer"]["objective"]
        self._populate_table(results)
        store.save_results(cfg, results, objective)  # save this run to history
        store.save_settings(self._collect_settings())
        self._refresh_recent()
        self._set_busy(False,
                       f"Done. {len(results)} configuration(s), ranked by {objective}.")

    # --- recent runs ----------------------------------------------------
    def _refresh_recent(self):
        self._recent = store.list_results()
        self.recent_combo["values"] = [e["label"] for e in self._recent]
        if self._recent:
            self.recent_combo.current(0)

    def _maybe_open_latest(self):
        # On startup just show the last results; keep the inputs restored from
        # saved settings rather than overwriting them.
        if getattr(self, "_recent", None):
            self._open_results_payload(
                store.load_results(self._recent[0]["path"]),
                message="Loaded most recent run.", update_inputs=False)

    def on_open_recent(self):
        idx = self.recent_combo.current()
        if idx < 0 or idx >= len(getattr(self, "_recent", [])):
            return
        try:
            payload = store.load_results(self._recent[idx]["path"])
        except (OSError, ValueError) as exc:
            messagebox.showerror("Could not open run", str(exc))
            return
        # Explicitly opening a past run also restores its inputs so it can be
        # tweaked and re-run.
        self._open_results_payload(payload, message="Loaded saved run.",
                                   update_inputs=True)

    def _open_results_payload(self, payload, message="Loaded.", update_inputs=False):
        cfg = payload.get("config", {})
        try:  # JSON turned the mass-bounds tuple into a list
            cfg["optimizer"]["mass_bounds"] = tuple(cfg["optimizer"]["mass_bounds"])
        except (KeyError, TypeError):
            pass
        self.cfg = cfg
        self.results = payload.get("results", [])
        self._populate_table(self.results)
        if update_inputs:
            self._apply_config(cfg)
            self._set_chosen_from_results(self.results)
        self.status.config(text=message)
        self.details_btn.config(
            state=tk.NORMAL if self.results else tk.DISABLED)

    def _set_chosen_from_results(self, results):
        """Set the Chosen list to the motors used in a loaded run."""
        self.chosen = {}
        for r in results:
            path = r["motor_file"]
            name = motor_name(path)
            self.all_motors.setdefault(name, path)
            self.chosen[name] = self.all_motors[name]
        self._refresh_chosen()

    def _run_failed(self, exc):
        self._set_busy(False, "Error.")
        messagebox.showerror("Optimization failed", str(exc))

    # --- full details for a selected config -----------------------------
    def on_show_details(self):
        if self._busy or not self.results:
            return
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("No selection", "Select a configuration first.")
            return
        result = self.results[int(sel[0])]
        self._set_busy(True, f"Simulating {motor_name(result['motor_file'])}...")
        threading.Thread(target=self._details_worker, args=(result,),
                         daemon=True).start()

    def _details_worker(self, result):
        try:
            env, flight = run(self.cfg, motor_file=result["motor_file"],
                              mass=result["mass"])
            figures = generate_flight_figures(flight)
            summary = metrics(env, flight)
            os.makedirs(OUTPUT_DIR, exist_ok=True)
            export = os.path.join(
                OUTPUT_DIR,
                f"{motor_name(result['motor_file'])}_{result['mass']:.2f}kg.csv")
            flight.export_data(export)
        except Exception as exc:
            self.root.after(0, self._run_failed, exc)
            return
        self.root.after(0, self._details_done, result, figures, summary, export)

    def _details_done(self, result, figures, summary, export):
        self._set_busy(False, "Ready")
        self._open_details_window(result, figures, summary, export)

    def _open_details_window(self, result, figures, summary, export):
        win = tk.Toplevel(self.root)
        win.title(f"{motor_name(result['motor_file'])} @ {result['mass']:.2f} kg")
        win.geometry("980x720")
        nb = ttk.Notebook(win)
        nb.pack(fill=tk.BOTH, expand=True)

        summary_tab = ttk.Frame(nb, padding=12)
        nb.add(summary_tab, text="Summary")
        lines = [
            f"Motor:            {motor_name(result['motor_file'])}",
            f"Airframe mass:    {result['mass']:.3f} kg",
            "",
            f"Apogee (AGL):     {summary['apogee']:.1f} m",
            f"Apogee time:      {summary['apogee_time']:.2f} s",
            f"Max speed:        {summary['max_speed']:.1f} m/s",
            f"Max Mach:         {summary['max_mach']:.2f}",
            f"Max acceleration: {summary['max_acceleration']:.1f} m/s^2",
            "",
            f"Time series exported to: {export}",
        ]
        tk.Label(summary_tab, text="\n".join(lines), justify=tk.LEFT,
                 font=("Courier New", 11), anchor="nw").pack(anchor=tk.NW)

        for name, fig in figures:
            tab = ttk.Frame(nb)
            nb.add(tab, text=name)
            canvas = FigureCanvasTkAgg(fig, master=tab)
            canvas.draw()
            NavigationToolbar2Tk(canvas, tab).update()
            canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

    # --- helpers --------------------------------------------------------
    def _set_busy(self, busy, message):
        self._busy = busy
        self.status.config(text=message)
        self.run_btn.config(state=tk.DISABLED if busy else tk.NORMAL)
        self.details_btn.config(
            state=tk.DISABLED if busy or not self.results else tk.NORMAL)


class AddMotorDialog:
    """Modal dialog to add a motor by pasting .eng text or browsing to a file."""

    def __init__(self, parent, on_success):
        self.on_success = on_success
        self.win = tk.Toplevel(parent)
        self.win.title("Add new motor")
        self.win.geometry("560x460")
        self.win.transient(parent)
        self.win.grab_set()

        frm = ttk.Frame(self.win, padding=10)
        frm.pack(fill=tk.BOTH, expand=True)

        name_row = ttk.Frame(frm)
        name_row.pack(fill=tk.X)
        ttk.Label(name_row, text="Name:").pack(side=tk.LEFT)
        self.name_var = tk.StringVar()
        ttk.Entry(name_row, textvariable=self.name_var).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=4)
        ttk.Button(name_row, text="Browse .eng file…",
                   command=self._browse).pack(side=tk.LEFT)

        ttk.Label(frm, text="Paste RASP .eng content (header line + thrust points):"
                  ).pack(anchor=tk.W, pady=(8, 0))
        self.text = tk.Text(frm, height=16, wrap="none")
        self.text.pack(fill=tk.BOTH, expand=True)
        self.text.insert("1.0",
                         "; example\nName 98 1000 P 5.0 9.0 CTI\n0.05 6000\n3.5 0\n")

        btns = ttk.Frame(frm)
        btns.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(btns, text="Add", command=self._submit).pack(side=tk.RIGHT)
        ttk.Button(btns, text="Cancel", command=self.win.destroy).pack(
            side=tk.RIGHT, padx=4)

    def _browse(self):
        path = filedialog.askopenfilename(
            title="Select a .eng motor file",
            filetypes=[("RASP engine files", "*.eng"), ("All files", "*.*")])
        if not path:
            return
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            self.text.delete("1.0", tk.END)
            self.text.insert("1.0", f.read())
        if not self.name_var.get().strip():
            self.name_var.set(os.path.splitext(os.path.basename(path))[0])

    def _submit(self):
        name = self.name_var.get().strip()
        text = self.text.get("1.0", tk.END)
        if not name:
            messagebox.showerror("Name required", "Enter a motor name.", parent=self.win)
            return
        try:
            validate_eng_text(text)
            path = save_motor_text(text, name, SAVED_DIR)
        except (ValueError, OSError) as exc:
            messagebox.showerror("Invalid motor", str(exc), parent=self.win)
            return
        self.win.destroy()
        self.on_success(motor_name(path), path)


def main():
    root = tk.Tk()
    OptimizerGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
