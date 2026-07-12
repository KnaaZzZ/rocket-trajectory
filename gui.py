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
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg  # noqa: E402

import store  # noqa: E402
from optimizer import optimize  # noqa: E402
from simulation import (  # noqa: E402
    LIBRARY_DIR,
    SAVED_DIR,
    find_motor_files,
    generate_flight_figures,
    metrics,
    motor_catalog,
    motor_metadata,
    motor_name,
    run,
    save_motor_text,
    validate_eng_text,
)

OUTPUT_DIR = "output"

APP_TITLE = "Rocket Trajectory Optimizer by Yaroslav Knyazkov"

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
    ("Rocket", [
        (("rocket", "cda"), "Drag area C_d·A (m^2)", "float"),
    ]),
    ("Flight", [
        (("flight", "rail_length"), "Rail length (m)", "float"),
        (("flight", "inclination"), "Inclination (deg from horiz.)", "float"),
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
    ("rank", "#", 40),
    ("motor", "Motor", 180),
    ("mass", "Mass (kg)", 90),
    ("apogee", "Apogee (m)", 100),
    ("apogee_time", "Apogee Time (s)", 110),
    ("max_speed", "Maximum Speed (m/s)", 150),
    ("max_mach", "Maximum Mach", 120),
    ("max_acceleration", "Maximum Acceleration (m/s²)", 200),
]


class OptimizerGUI:
    def __init__(self, root):
        self.root = root
        root.title(APP_TITLE)
        root.minsize(1000, 640)
        root.geometry("1360x800")

        self.vars = {}          # config path -> tk StringVar
        self.field_widgets = {}  # config path -> (label, entry/combo) widgets
        self.results = []       # last optimize() results, index-aligned to rows
        self.all_motors = {}    # motor name -> .eng path (library + saved + added)
        self.chosen = {}        # motor name -> .eng path (optimizer runs these)
        self.cfg = None         # config used for the currently shown results
        self.preset_menus = {}  # group key -> tk.Menu of presets
        self._busy = False

        self.presets = store.load_presets()
        self.motor_presets = store.load_motor_presets()
        self.saved_configs = store.load_saved_configs()
        self.settings = store.load_settings()
        self.body = ttk.Frame(root)
        self.body.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
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
        left = ttk.Frame(self.body, padding=(12, 10))
        left.pack(side=tk.LEFT, fill=tk.Y)

        for group_name, fields in FIELDS:
            group_key = fields[0][0][0]  # section, e.g. "environment"
            box = ttk.LabelFrame(left, text=group_name, padding=6)
            box.pack(fill=tk.X, pady=3)
            for path, label, kind in fields:
                row = ttk.Frame(box)
                row.pack(fill=tk.X, pady=1)
                label_widget = ttk.Label(row, text=label, width=25)
                label_widget.pack(side=tk.LEFT)
                var = tk.StringVar()
                self.vars[path] = var
                if kind == "combo":
                    widget = ttk.Combobox(row, textvariable=var, values=OBJECTIVES,
                                          state="readonly", width=15)
                else:
                    widget = ttk.Entry(row, textvariable=var, width=17)
                widget.pack(side=tk.RIGHT)
                self.field_widgets[path] = (label_widget, widget)
            self._build_preset_menu(box, group_key)  # compact, below the inputs

        # Grey out objective-specific fields when their objective isn't chosen.
        self.vars[("optimizer", "objective")].trace_add(
            "write", lambda *a: self._update_conditional_fields())
        self._update_conditional_fields()

    # Fields that only apply to a specific objective.
    _CONDITIONAL_FIELDS = {
        ("optimizer", "mach_limit"): "apogee_capped_mach",
        ("optimizer", "target_altitude"): "min_mass_for_altitude",
    }

    def _update_conditional_fields(self):
        objective = self.vars[("optimizer", "objective")].get()
        for path, needed_by in self._CONDITIONAL_FIELDS.items():
            label, widget = self.field_widgets[path]
            active = objective == needed_by
            widget.configure(state="normal" if active else "disabled")
            label.configure(state="normal" if active else "disabled")

    @staticmethod
    def _link_menu(parent, text, postcommand):
        """A small blue text link that drops a menu (compact preset control)."""
        mb = tk.Menubutton(parent, text=text, relief=tk.FLAT, borderwidth=0,
                           fg="#3b6ea5", activeforeground="#1f4e79",
                           cursor="hand2", font=("", 8), padx=0, pady=0)
        menu = tk.Menu(mb, tearoff=0)
        mb["menu"] = menu
        menu.configure(postcommand=postcommand)
        return mb, menu

    def _build_preset_menu(self, box, group_key):
        """A small 'Presets' link that drops a load/save/delete menu."""
        mb, menu = self._link_menu(
            box, "Presets ▾", lambda: self._populate_preset_menu(group_key))
        mb.pack(anchor=tk.E, pady=(2, 0))
        self.preset_menus[group_key] = menu

    # --- motor panel ----------------------------------------------------
    def _build_motor_panel(self):
        mid = ttk.Frame(self.body, padding=(12, 10))
        mid.pack(side=tk.LEFT, fill=tk.Y)

        ttk.Label(mid, text="Motors", font=("", 10, "bold")).pack(anchor=tk.W)
        ttk.Button(mid, text="Choose motors…  (browse library)",
                   command=self._open_motor_browser).pack(fill=tk.X, pady=(2, 4))

        self.chosen_label = ttk.Label(mid, text="Chosen (optimizer runs these): 0")
        self.chosen_label.pack(anchor=tk.W)
        chosen_box = ttk.Frame(mid)
        chosen_box.pack(fill=tk.BOTH, expand=True)
        self.chosen_list = tk.Listbox(chosen_box, selectmode=tk.EXTENDED, width=32,
                                      height=16, exportselection=False)
        csb = ttk.Scrollbar(chosen_box, command=self.chosen_list.yview)
        self.chosen_list.config(yscrollcommand=csb.set)
        self.chosen_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        csb.pack(side=tk.RIGHT, fill=tk.Y)
        self.chosen_list.bind("<Double-1>", lambda e: self._remove_selected())

        btns = ttk.Frame(mid)
        btns.pack(fill=tk.X, pady=3)
        ttk.Button(btns, text="Remove selected", command=self._remove_selected).pack(
            side=tk.LEFT, expand=True, fill=tk.X)
        ttk.Button(btns, text="Clear all", command=self._clear_chosen).pack(
            side=tk.LEFT, expand=True, fill=tk.X)

        preset = ttk.Frame(mid)
        preset.pack(fill=tk.X, pady=(4, 0))
        mb, menu = self._link_menu(preset, "Motor presets ▾",
                                   self._populate_motor_preset_menu)
        mb.pack(anchor=tk.E)
        self.motor_preset_menu = menu

    # --- results panel --------------------------------------------------
    def _build_results_panel(self):
        right = ttk.Frame(self.body, padding=(12, 10))
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
        table = ttk.Frame(right)
        table.pack(fill=tk.BOTH, expand=True, pady=6)
        cols = [c[0] for c in TABLE_COLUMNS]
        self.tree = ttk.Treeview(table, columns=cols, show="headings", height=20)
        for key, heading, width in TABLE_COLUMNS:
            self.tree.heading(key, text=heading)
            self.tree.column(key, width=width, anchor=tk.W if key == "motor" else tk.E)
        vsb = ttk.Scrollbar(table, orient=tk.VERTICAL, command=self.tree.yview)
        hsb = ttk.Scrollbar(table, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        table.rowconfigure(0, weight=1)
        table.columnconfigure(0, weight=1)
        self.tree.bind("<Double-1>", lambda e: self.on_show_details())

        actions = ttk.Frame(right)
        actions.pack(fill=tk.X, pady=(2, 0))
        self.details_btn = ttk.Button(
            actions, text="Show data & plots (or double-click a row)",
            command=self.on_show_details, state=tk.DISABLED)
        self.details_btn.pack(side=tk.LEFT)
        ttk.Button(actions, text="Save configuration",
                   command=self.on_save_config).pack(side=tk.LEFT, padx=6)

        saved = ttk.Frame(right)
        saved.pack(fill=tk.X, pady=(4, 0))
        ttk.Label(saved, text="Saved configs:").pack(side=tk.LEFT)
        self.saved_combo = ttk.Combobox(saved, state="readonly")
        self.saved_combo.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4)
        ttk.Button(saved, text="Open", command=self.on_open_saved_config).pack(
            side=tk.LEFT)
        ttk.Button(saved, text="Del", command=self.on_delete_saved_config).pack(
            side=tk.LEFT)
        self._refresh_saved_combo()

    # --- motor list logic -----------------------------------------------
    def _load_library(self):
        self.all_motors = {}
        for directory in (LIBRARY_DIR, SAVED_DIR):
            for path in find_motor_files(directory):
                self.all_motors.setdefault(motor_name(path), path)

    def _open_motor_browser(self):
        MotorBrowser(self.root, self._add_records, self._remove_motor_names)

    def _add_records(self, records):
        """Add motor metadata records (from the browser) to the chosen set."""
        for r in records:
            name = r["name"]
            self.all_motors.setdefault(name, r["path"])
            self.chosen[name] = r["path"]
        self._refresh_chosen()

    def _remove_motor_names(self, names):
        """A saved motor was deleted in the browser; drop it from the chosen set."""
        for name in names:
            self.chosen.pop(name, None)
            self.all_motors.pop(name, None)
        self._refresh_chosen()

    def _add_names(self, names):
        for name in names:
            if name in self.all_motors:
                self.chosen[name] = self.all_motors[name]
        self._refresh_chosen()

    def _remove_selected(self):
        names = [self._chosen_names[i] for i in self.chosen_list.curselection()]
        for name in names:
            self.chosen.pop(name, None)
        self._refresh_chosen()

    def _clear_chosen(self):
        if self.chosen and messagebox.askyesno(
                "Clear all", "Remove all motors from the chosen list?"):
            self.chosen = {}
            self._refresh_chosen()

    def _refresh_chosen(self):
        self._chosen_names = sorted(self.chosen)
        self.chosen_list.delete(0, tk.END)
        for name in self._chosen_names:
            self.chosen_list.insert(tk.END, name)
        self.chosen_label.config(
            text=f"Chosen (optimizer runs these): {len(self.chosen)}")

    # --- motor presets (named sets of chosen motors) --------------------
    def _populate_motor_preset_menu(self):
        menu = self.motor_preset_menu
        menu.delete(0, tk.END)
        names = sorted(self.motor_presets)
        for name in names:
            menu.add_command(label=f"Load  {name}",
                             command=lambda n=name: self._load_motor_preset(n))
        if names:
            menu.add_separator()
        menu.add_command(label="Save current motors as new preset…",
                         command=self._save_motor_preset)
        if names:
            submenu = tk.Menu(menu, tearoff=0)
            for name in names:
                submenu.add_command(
                    label=name, command=lambda n=name: self._delete_motor_preset(n))
            menu.add_cascade(label="Delete", menu=submenu)

    def _save_motor_preset(self):
        if not self.chosen:
            messagebox.showinfo("No motors", "Choose motors before saving a preset.")
            return
        name = simpledialog.askstring("Save motor preset",
                                      "Name for this motor set:", parent=self.root)
        if not name or not name.strip():
            return
        self.motor_presets[name.strip()] = sorted(self.chosen)
        store.save_motor_presets(self.motor_presets)

    def _load_motor_preset(self, name):
        names = self.motor_presets.get(name)
        if not names:
            return
        self.chosen = {}
        missing = [n for n in names if n not in self.all_motors]
        self._add_names(names)
        if missing:
            messagebox.showwarning("Some motors missing",
                                   f"{len(missing)} motor(s) in this preset are no "
                                   "longer in the library and were skipped.")

    def _delete_motor_preset(self, name):
        if name in self.motor_presets and messagebox.askyesno(
                "Delete motor preset", f"Delete motor preset '{name}'?"):
            del self.motor_presets[name]
            store.save_motor_presets(self.motor_presets)

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

    def _populate_preset_menu(self, group_key):
        """Rebuild a group's preset menu each time it opens."""
        menu = self.preset_menus[group_key]
        menu.delete(0, tk.END)
        names = sorted(self.presets.get(group_key, {}))
        for name in names:
            menu.add_command(label=f"Load  {name}",
                             command=lambda n=name: self._load_preset(group_key, n))
        if names:
            menu.add_separator()
        menu.add_command(label="Save current as new preset…",
                         command=lambda: self._save_preset(group_key))
        if names:
            submenu = tk.Menu(menu, tearoff=0)
            for name in names:
                submenu.add_command(
                    label=name, command=lambda n=name: self._delete_preset(group_key, n))
            menu.add_cascade(label="Delete", menu=submenu)

    def _save_preset(self, group_key):
        name = simpledialog.askstring(
            "Save preset", f"Name for this {group_key} preset:", parent=self.root)
        if not name or not name.strip():
            return
        values = {self._path_key(p): self.vars[p].get()
                  for p in self._group_paths(group_key)}
        self.presets.setdefault(group_key, {})[name.strip()] = values
        store.save_presets(self.presets)

    def _load_preset(self, group_key, name):
        preset = self.presets.get(group_key, {}).get(name)
        if not preset:
            return
        for p in self._group_paths(group_key):
            if self._path_key(p) in preset:
                self.vars[p].set(preset[self._path_key(p)])

    def _delete_preset(self, group_key, name):
        if name in self.presets.get(group_key, {}) and messagebox.askyesno(
                "Delete preset", f"Delete {group_key} preset '{name}'?"):
            del self.presets[group_key][name]
            store.save_presets(self.presets)

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
        ("rocket", "cda"): ("Drag area C_d·A", lambda v: v > 0, "must be > 0"),
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

    # --- full details for a configuration -------------------------------
    def on_show_details(self):
        if self._busy or not self.results:
            return
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("No selection", "Select a configuration first.")
            return
        result = self.results[int(sel[0])]
        self._start_details(self.cfg, result["motor_file"], result["mass"])

    def _start_details(self, config, motor_file, mass):
        self._set_busy(True, f"Simulating {motor_name(motor_file)}...")
        threading.Thread(target=self._details_worker,
                         args=(config, motor_file, mass), daemon=True).start()

    def _details_worker(self, config, motor_file, mass):
        try:
            env, flight = run(config, motor_file=motor_file, mass=mass)
            figures = generate_flight_figures(flight)
            summary = metrics(env, flight)
            os.makedirs(OUTPUT_DIR, exist_ok=True)
            export = os.path.join(
                OUTPUT_DIR, f"{motor_name(motor_file)}_{mass:.2f}kg.csv")
            flight.export_data(export)
        except Exception as exc:
            self.root.after(0, self._run_failed, exc)
            return
        self.root.after(0, self._details_done, motor_file, mass, figures,
                        summary, export)

    def _details_done(self, motor_file, mass, figures, summary, export):
        self._set_busy(False, "Ready")
        self._open_details_window(motor_file, mass, figures, summary, export)

    def _open_details_window(self, motor_file, mass, figures, summary, export):
        win = tk.Toplevel(self.root)
        win.title(f"{motor_name(motor_file)} @ {mass:.2f} kg")
        win.geometry("1000x760")

        # One scrollable page holding the summary and every plot stacked.
        canvas = tk.Canvas(win, highlightthickness=0)
        vsb = ttk.Scrollbar(win, orient=tk.VERTICAL, command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        content = ttk.Frame(canvas)
        win_id = canvas.create_window((0, 0), window=content, anchor="nw")
        content.bind("<Configure>",
                     lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(win_id, width=e.width))

        def on_wheel(e):
            canvas.yview_scroll(int(-e.delta / 120), "units")
        canvas.bind("<MouseWheel>", on_wheel)

        lines = [
            f"Motor:            {motor_name(motor_file)}",
            f"Airframe mass:    {mass:.3f} kg",
            "",
            f"Apogee (AGL):     {summary['apogee']:.1f} m",
            f"Apogee time:      {summary['apogee_time']:.2f} s",
            f"Max speed:        {summary['max_speed']:.1f} m/s",
            f"Max Mach:         {summary['max_mach']:.2f}",
            f"Max acceleration: {summary['max_acceleration']:.1f} m/s^2",
            "",
            f"Time series exported to: {export}",
        ]
        tk.Label(content, text="\n".join(lines), justify=tk.LEFT,
                 font=("Courier New", 11), anchor="nw").pack(anchor=tk.NW, padx=12,
                                                             pady=8)

        for name, fig in figures:
            ttk.Label(content, text=name, font=("", 10, "bold")).pack(
                anchor=tk.W, padx=12, pady=(10, 0))
            fig_canvas = FigureCanvasTkAgg(fig, master=content)
            fig_canvas.draw()
            widget = fig_canvas.get_tk_widget()
            widget.pack(fill=tk.X, padx=12, pady=(0, 6))
            widget.bind("<MouseWheel>", on_wheel)  # keep wheel scrolling over plots

    # --- saved configurations -------------------------------------------
    def on_save_config(self):
        if not self.results:
            return
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("No selection", "Select a configuration to save.")
            return
        result = self.results[int(sel[0])]
        name = simpledialog.askstring(
            "Save configuration", "Name for this configuration:", parent=self.root)
        if not name or not name.strip():
            return
        self.saved_configs[name.strip()] = {
            "motor_name": motor_name(result["motor_file"]),
            "motor_file": result["motor_file"],
            "mass": result["mass"],
            "metrics": result["metrics"],
            "config": self.cfg,
        }
        store.save_saved_configs(self.saved_configs)
        self._refresh_saved_combo()
        self.saved_combo.set(name.strip())

    def _refresh_saved_combo(self):
        names = sorted(self.saved_configs)
        self.saved_combo["values"] = names
        if self.saved_combo.get() not in names:
            self.saved_combo.set("")

    def on_open_saved_config(self):
        if self._busy:
            return
        rec = self.saved_configs.get(self.saved_combo.get())
        if not rec:
            messagebox.showinfo("No configuration", "Pick a saved configuration.")
            return
        cfg = rec["config"]
        try:
            cfg["optimizer"]["mass_bounds"] = tuple(cfg["optimizer"]["mass_bounds"])
        except (KeyError, TypeError):
            pass
        self._start_details(cfg, rec["motor_file"], rec["mass"])

    def on_delete_saved_config(self):
        name = self.saved_combo.get()
        if name in self.saved_configs and messagebox.askyesno(
                "Delete configuration", f"Delete saved configuration '{name}'?"):
            del self.saved_configs[name]
            store.save_saved_configs(self.saved_configs)
            self._refresh_saved_combo()

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


class DualRangeSlider(ttk.Frame):
    """A single-track slider with two thumbs (min & max) plus min/max entries.

    Drag either thumb on the shared track, or type exact values in the entry
    boxes. ``fmt`` renders a value for the entries; ``parse`` turns entry text
    back into a value (returns None if invalid); ``snap`` optionally quantizes
    dragged/typed values (e.g. round for integers). get() returns (low, high).
    """

    PAD = 10
    HEIGHT = 26
    RADIUS = 6

    def __init__(self, master, lo, hi, on_change, fmt=None, parse=None, snap=None):
        super().__init__(master)
        self.lo, self.hi = float(lo), float(hi)
        if self.hi <= self.lo:
            self.hi = self.lo + 1.0
        self.on_change = on_change
        self.fmt = fmt or (lambda v: f"{v:.0f}")
        self.parse = parse or self._default_parse
        self.snap = snap
        self.low, self.high = self.lo, self.hi
        self._pxw = 190
        self._active = None

        entries = ttk.Frame(self)
        entries.pack(fill=tk.X)
        self.min_var = tk.StringVar()
        self.max_var = tk.StringVar()
        me = ttk.Entry(entries, textvariable=self.min_var, width=8)
        me.pack(side=tk.LEFT)
        ttk.Label(entries, text="–").pack(side=tk.LEFT, padx=2)
        xe = ttk.Entry(entries, textvariable=self.max_var, width=8)
        xe.pack(side=tk.RIGHT)
        for entry in (me, xe):
            entry.bind("<Return>", lambda e: self._entries_changed())
            entry.bind("<FocusOut>", lambda e: self._entries_changed())

        self.canvas = tk.Canvas(self, height=self.HEIGHT, highlightthickness=0)
        self.canvas.pack(fill=tk.X)
        self.canvas.bind("<Configure>", self._on_resize)
        self.canvas.bind("<Button-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)

        self._sync_entries()
        self.after(0, self._redraw)

    # --- value <-> pixel -------------------------------------------------
    @staticmethod
    def _default_parse(text):
        try:
            return float(text)
        except (TypeError, ValueError):
            return None

    def _clamp(self, v):
        if self.snap:
            v = self.snap(v)
        return min(max(v, self.lo), self.hi)

    def _x_of(self, v):
        frac = (v - self.lo) / (self.hi - self.lo)
        return self.PAD + frac * (self._pxw - 2 * self.PAD)

    def _v_of(self, x):
        frac = (x - self.PAD) / max(self._pxw - 2 * self.PAD, 1)
        return self._clamp(self.lo + min(max(frac, 0.0), 1.0) * (self.hi - self.lo))

    # --- drawing / interaction ------------------------------------------
    def _on_resize(self, event):
        # Debounce: coalesce the redraw so dragging the window edge isn't laggy.
        self._pxw = event.width
        if getattr(self, "_resize_job", None):
            self.after_cancel(self._resize_job)
        self._resize_job = self.after(30, self._redraw)

    def _redraw(self):
        c = self.canvas
        c.delete("all")
        y = self.HEIGHT // 2
        xlo, xhi = self._x_of(self.low), self._x_of(self.high)
        c.create_line(self.PAD, y, self._pxw - self.PAD, y, fill="#aaa", width=3)
        c.create_line(xlo, y, xhi, y, fill="#3aa76d", width=3)
        r = self.RADIUS
        c.create_oval(xlo - r, y - r, xlo + r, y + r, fill="white",
                      outline="#444", width=2, tags="min")
        c.create_oval(xhi - r, y - r, xhi + r, y + r, fill="white",
                      outline="#444", width=2, tags="max")

    def _on_press(self, event):
        # grab whichever thumb is nearer the click, then move it there
        self._active = ("min" if abs(event.x - self._x_of(self.low))
                        <= abs(event.x - self._x_of(self.high)) else "max")
        self._on_drag(event)

    def _on_drag(self, event):
        if not self._active:
            return
        v = self._v_of(event.x)
        if self._active == "min":
            self.low = min(v, self.high)
        else:
            self.high = max(v, self.low)
        self._sync_entries()
        self._redraw()
        self.on_change()

    # --- entries ---------------------------------------------------------
    def _entries_changed(self):
        lo, hi = self.parse(self.min_var.get()), self.parse(self.max_var.get())
        if lo is not None:
            self.low = self._clamp(lo)
        if hi is not None:
            self.high = self._clamp(hi)
        if self.low > self.high:
            self.low, self.high = self.high, self.low
        self._sync_entries()
        self._redraw()
        self.on_change()

    def _sync_entries(self):
        self.min_var.set(self.fmt(self.low))
        self.max_var.set(self.fmt(self.high))

    # --- public ----------------------------------------------------------
    def get(self):
        return self.low, self.high

    def reset(self):
        self.low, self.high = self.lo, self.hi
        self._sync_entries()
        self._redraw()


class MotorBrowser:
    """OpenRocket-style motor browser: search, side filters, sortable columns.

    Reads the local motor library (data/library + data/saved) via
    simulation.motor_catalog and lets the user add motors to the chosen set.
    """

    # (key, heading, width, kind). kind drives sorting/formatting.
    COLUMNS = [
        ("manufacturer", "Manufacturer", 110, "text"),
        ("designation", "Motor", 150, "text"),
        ("impulse_class", "Class", 60, "class"),
        ("total_impulse", "Total Impulse (Ns)", 130, "num0"),
        ("avg_thrust", "Average Thrust (N)", 130, "num0"),
        ("diameter_mm", "Diameter (mm)", 110, "num0"),
        ("length_mm", "Length (mm)", 100, "num0"),
        ("burn_time", "Burn Time (s)", 100, "num2"),
    ]
    _NUMERIC = {"total_impulse", "avg_thrust", "diameter_mm", "length_mm", "burn_time"}

    def __init__(self, parent, on_add, on_delete=None):
        self.on_add = on_add
        self.on_delete = on_delete
        self.catalog = motor_catalog()
        saved_abs = os.path.abspath(SAVED_DIR)
        for r in self.catalog:  # tag each motor with its source directory
            r["source"] = ("saved"
                           if os.path.abspath(r["path"]).startswith(saved_abs)
                           else "library")
        self.library_records = [r for r in self.catalog if r["source"] == "library"]
        self.saved_records = [r for r in self.catalog if r["source"] == "saved"]
        self._sources = ["library", "saved"]
        self.sort_col = "manufacturer"
        self.sort_reverse = False
        self._shown = {"library": [], "saved": []}

        self.win = tk.Toplevel(parent)
        self.win.title("Choose motors")
        self.win.geometry("1180x700")
        self.win.minsize(900, 560)
        self.win.transient(parent)
        self.win.grab_set()

        # Footer first so it reserves the bottom strip (full width); otherwise
        # the left-packed filters/table push the Add/Close buttons off-screen.
        self._build_footer()
        self._build_filters()
        self._build_tables()
        self._refresh()

    # --- widgets --------------------------------------------------------
    def _build_filters(self):
        # Scrollable side panel so the filters never get cramped.
        outer = ttk.Frame(self.win, padding=(8, 8, 2, 8))
        outer.pack(side=tk.LEFT, fill=tk.Y)
        canvas = tk.Canvas(outer, width=236, highlightthickness=0)
        vsb = ttk.Scrollbar(outer, orient=tk.VERTICAL, command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.Y)
        side = ttk.Frame(canvas, padding=(0, 0, 8, 0))
        win_id = canvas.create_window((0, 0), window=side, anchor="nw")
        side.bind("<Configure>",
                  lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(win_id, width=e.width))
        canvas.bind("<MouseWheel>",
                    lambda e: canvas.yview_scroll(int(-e.delta / 120), "units"))

        ttk.Label(side, text="Filters", font=("", 11, "bold")).pack(anchor=tk.W)

        # Search --------------------------------------------------------
        ttk.Label(side, text="Search").pack(anchor=tk.W, pady=(10, 0))
        self.search_var = tk.StringVar()
        ttk.Entry(side, textvariable=self.search_var).pack(fill=tk.X)
        self.search_var.trace_add("write", lambda *a: self._refresh())

        # Manufacturers: multi-checkbox --------------------------------
        ttk.Separator(side).pack(fill=tk.X, pady=10)
        header = ttk.Frame(side)
        header.pack(fill=tk.X)
        ttk.Label(header, text="Manufacturers").pack(side=tk.LEFT)
        ttk.Button(header, text="None", width=5,
                   command=lambda: self._set_all_manufacturers(False)).pack(side=tk.RIGHT)
        ttk.Button(header, text="All", width=4,
                   command=lambda: self._set_all_manufacturers(True)).pack(side=tk.RIGHT)
        self.manuf_vars = {}
        for m in sorted({r["manufacturer"] for r in self.catalog}):
            var = tk.BooleanVar(value=True)
            self.manuf_vars[m] = var
            ttk.Checkbutton(side, text=m, variable=var,
                            command=self._refresh).pack(anchor=tk.W)

        # Range sliders -------------------------------------------------
        ttk.Separator(side).pack(fill=tk.X, pady=10)
        ttk.Label(side, text="Ranges").pack(anchor=tk.W)
        self._classes = sorted({r["impulse_class"] for r in self.catalog},
                               key=self._class_order)
        self._class_index = {c: i for i, c in enumerate(self._classes)}

        def bounds(key):
            values = [r[key] for r in self.catalog] or [0.0]
            return min(values), max(values)

        def slider_section(text, slider):
            ttk.Label(side, text=text).pack(anchor=tk.W, pady=(10, 2))
            slider.pack(fill=tk.X)

        self.class_slider = DualRangeSlider(
            side, 0, max(len(self._classes) - 1, 1), self._refresh,
            fmt=lambda v: self._classes[min(int(round(v)), len(self._classes) - 1)],
            parse=self._parse_class, snap=round)
        slider_section("Class", self.class_slider)

        self.imp_slider = DualRangeSlider(side, *bounds("total_impulse"),
                                          self._refresh, snap=round)
        slider_section("Total impulse (Ns)", self.imp_slider)

        self.avg_slider = DualRangeSlider(side, *bounds("avg_thrust"),
                                          self._refresh, snap=round)
        slider_section("Average thrust (N)", self.avg_slider)

        self.dia_slider = DualRangeSlider(side, *bounds("diameter_mm"),
                                          self._refresh, snap=round)
        slider_section("Diameter (mm)", self.dia_slider)

        self.len_slider = DualRangeSlider(side, *bounds("length_mm"),
                                          self._refresh, snap=round)
        slider_section("Length (mm)", self.len_slider)

        ttk.Separator(side).pack(fill=tk.X, pady=10)
        ttk.Button(side, text="Reset filters", command=self._reset_filters).pack(
            fill=tk.X)
        self.count_label = ttk.Label(side, text="")
        self.count_label.pack(anchor=tk.W, pady=(8, 0))

    def _set_all_manufacturers(self, value):
        for var in self.manuf_vars.values():
            var.set(value)
        self._refresh()

    def _build_tables(self):
        container = ttk.Frame(self.win, padding=(0, 8))
        container.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.notebook = ttk.Notebook(container)
        self.notebook.pack(fill=tk.BOTH, expand=True)
        titles = {"library": "ThrustCurve library", "saved": "My saved motors"}
        cols = [c[0] for c in self.COLUMNS]
        self.tables = {}
        for source in self._sources:
            tab = ttk.Frame(self.notebook)
            self.notebook.add(tab, text=titles[source])
            tree = ttk.Treeview(tab, columns=cols, show="headings",
                                selectmode=tk.EXTENDED)
            for key, heading, width, kind in self.COLUMNS:
                tree.heading(key, text=heading,
                             command=lambda k=key: self._sort_by(k))
                tree.column(key, width=width,
                            anchor=tk.W if kind == "text" else tk.E)
            vsb = ttk.Scrollbar(tab, command=tree.yview)
            tree.config(yscrollcommand=vsb.set)
            tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            vsb.pack(side=tk.RIGHT, fill=tk.Y)
            tree.bind("<Double-1>", lambda e: self._add_selected())
            self.tables[source] = tree
        self.notebook.bind("<<NotebookTabChanged>>",
                           lambda e: self._on_tab_changed())

    def _active_source(self):
        return self._sources[self.notebook.index("current")]

    def _on_tab_changed(self):
        self._update_count()
        self._update_delete_state()

    def _build_footer(self):
        bar = ttk.Frame(self.win, padding=8)
        bar.pack(side=tk.BOTTOM, fill=tk.X)
        ttk.Button(bar, text="New motor…", command=self._add_new_motor).pack(
            side=tk.LEFT)
        self.delete_btn = ttk.Button(bar, text="Delete selected",
                                     command=self._delete_selected)
        self.delete_btn.pack(side=tk.LEFT, padx=6)
        self.status = ttk.Label(bar, text="")
        self.status.pack(side=tk.LEFT, padx=8)
        ttk.Button(bar, text="Close", command=self.win.destroy).pack(side=tk.RIGHT)
        ttk.Button(bar, text="Add all shown", command=self._add_all_shown).pack(
            side=tk.RIGHT, padx=4)
        ttk.Button(bar, text="Add selected", command=self._add_selected).pack(
            side=tk.RIGHT)
        self._update_delete_state()

    def _add_new_motor(self):
        AddMotorDialog(self.win, self._on_new_motor)

    def _on_new_motor(self, name, path):
        """A pasted/loaded motor was validated and saved; catalog it and add it."""
        try:
            record = motor_metadata(path)
        except (ValueError, IndexError, OSError) as exc:
            messagebox.showerror("Motor error", str(exc), parent=self.win)
            return
        record["source"] = "saved"
        self.catalog.append(record)
        self.saved_records.append(record)
        self._refresh()
        self.notebook.select(self._sources.index("saved"))  # show it on the Saved tab
        self.on_add([record])
        self.status.config(text=f"Added new motor '{name}'.")

    # --- behavior -------------------------------------------------------
    @staticmethod
    def _class_order(cls):
        return -1 if cls == "<A" else (ord(cls[0]) if cls and cls[0].isalpha() else 99)

    def _parse_class(self, text):
        """Class entry accepts a letter (e.g. 'N') or a numeric index."""
        text = text.strip().upper()
        if text in self._class_index:
            return self._class_index[text]
        try:
            return float(text)
        except ValueError:
            return None

    def _reset_filters(self):
        self.search_var.set("")
        self._set_all_manufacturers(True)  # also refreshes
        for slider in (self.class_slider, self.imp_slider, self.avg_slider,
                       self.dia_slider, self.len_slider):
            slider.reset()
        self._refresh()

    def _passes(self, r):
        text = self.search_var.get().strip().lower()
        if text and text not in f"{r['manufacturer']} {r['designation']} {r['name']}".lower():
            return False
        var = self.manuf_vars.get(r["manufacturer"])
        if var is not None and not var.get():  # unknown manufacturers pass
            return False
        clo, chi = self.class_slider.get()
        cidx = self._class_index.get(r["impulse_class"], -1)
        if not (round(clo) <= cidx <= round(chi)):
            return False
        for slider, key in [(self.imp_slider, "total_impulse"),
                            (self.avg_slider, "avg_thrust"),
                            (self.dia_slider, "diameter_mm"),
                            (self.len_slider, "length_mm")]:
            lo, hi = slider.get()
            if not (lo <= r[key] <= hi):
                return False
        return True

    def _sort_by(self, col):
        if col == self.sort_col:
            self.sort_reverse = not self.sort_reverse
        else:
            self.sort_col = col
            self.sort_reverse = False
        self._refresh()

    def _sort_key(self, r):
        col = self.sort_col
        if col == "impulse_class":
            return r["total_impulse"]  # order classes by actual impulse
        if col in self._NUMERIC:
            return r[col]
        return str(r[col]).lower()

    def _format(self, r):
        out = []
        for key, _, _, kind in self.COLUMNS:
            v = r[key]
            if kind == "num0":
                out.append(f"{v:.0f}")
            elif kind == "num2":
                out.append(f"{v:.2f}")
            else:
                out.append(v)
        return out

    def _refresh(self):
        for source in self._sources:
            records = (self.library_records if source == "library"
                       else self.saved_records)
            shown = sorted((r for r in records if self._passes(r)),
                           key=self._sort_key, reverse=self.sort_reverse)
            self._shown[source] = shown
            tree = self.tables[source]
            tree.delete(*tree.get_children())
            for i, r in enumerate(shown):
                tree.insert("", tk.END, iid=str(i), values=self._format(r))
        self._update_count()
        self._update_delete_state()

    def _update_count(self):
        source = self._active_source()
        total = len(self.library_records if source == "library"
                    else self.saved_records)
        self.count_label.config(text=f"{len(self._shown[source])} of {total} shown")

    def _update_delete_state(self):
        if hasattr(self, "delete_btn") and hasattr(self, "notebook"):
            self.delete_btn.config(
                state=tk.NORMAL if self._active_source() == "saved" else tk.DISABLED)

    def _add_records(self, records):
        if not records:
            messagebox.showinfo("No selection", "Select motors first.", parent=self.win)
            return
        self.on_add(records)
        self.status.config(text=f"Added {len(records)} motor(s).")

    def _add_selected(self):
        source = self._active_source()
        tree = self.tables[source]
        self._add_records([self._shown[source][int(i)] for i in tree.selection()])

    def _add_all_shown(self):
        self._add_records(list(self._shown[self._active_source()]))

    def _delete_selected(self):
        """Delete the selected saved motors from disk (library motors can't be)."""
        tree = self.tables["saved"]
        records = [self._shown["saved"][int(i)] for i in tree.selection()]
        if not records:
            messagebox.showinfo("No selection", "Select saved motors to delete.",
                                parent=self.win)
            return
        if not messagebox.askyesno(
                "Delete motors",
                f"Delete {len(records)} saved motor(s) from disk? This cannot be undone.",
                parent=self.win):
            return
        names = []
        for r in records:
            try:
                os.remove(r["path"])
            except OSError:
                pass
            if r in self.catalog:
                self.catalog.remove(r)
            if r in self.saved_records:
                self.saved_records.remove(r)
            names.append(r["name"])
        if self.on_delete:
            self.on_delete(names)  # let the main window drop them from Chosen
        self._refresh()
        self.status.config(text=f"Deleted {len(names)} motor(s).")


def main():
    root = tk.Tk()
    OptimizerGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
