"""Tkinter GUI for the point-mass trajectory optimizer.

Left panel: edit the configuration and run the optimizer.
Right panel: the ranked table of configurations. Double-click (or select +
"Show data & plots") a row to run that configuration in full -- its numeric
summary and every flight plot open in a tabbed window, and the time series is
exported to CSV.

Run with:  python gui.py
"""

import copy
import os
import threading
import tkinter as tk
from tkinter import messagebox, ttk

import matplotlib

matplotlib.use("Agg")  # figures are embedded via FigureCanvasTkAgg, not shown
from matplotlib.backends.backend_tkagg import (  # noqa: E402
    FigureCanvasTkAgg,
    NavigationToolbar2Tk,
)

from config import CONFIG  # noqa: E402
from optimizer import _motor_name, optimize  # noqa: E402
from simulation import generate_flight_figures, metrics, run  # noqa: E402

OUTPUT_DIR = "output"

OBJECTIVES = [
    "apogee",
    "apogee_time",
    "max_speed",
    "max_mach",
    "max_acceleration",
    "apogee_capped_mach",
    "min_mass_for_altitude",
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
    ("rank", "#", 40),
    ("motor", "Motor", 190),
    ("mass", "Mass (kg)", 80),
    ("apogee", "Apogee (m)", 95),
    ("apogee_time", "Apo t (s)", 75),
    ("max_speed", "MaxV (m/s)", 85),
    ("max_mach", "Max Mach", 80),
    ("max_acceleration", "MaxAcc (m/s^2)", 105),
]


class OptimizerGUI:
    def __init__(self, root):
        self.root = root
        root.title("Point-Mass Trajectory Optimizer")
        root.geometry("1100x640")

        self.vars = {}          # config path -> tk StringVar
        self.results = []       # last optimize() results, index-aligned to tree rows
        self._busy = False

        self._build_config_panel()
        self._build_results_panel()
        self._load_defaults(CONFIG)

    # --- layout ---------------------------------------------------------
    def _build_config_panel(self):
        left = ttk.Frame(self.root, padding=10)
        left.pack(side=tk.LEFT, fill=tk.Y)

        for group_name, fields in FIELDS:
            box = ttk.LabelFrame(left, text=group_name, padding=8)
            box.pack(fill=tk.X, pady=4)
            for path, label, kind in fields:
                row = ttk.Frame(box)
                row.pack(fill=tk.X, pady=1)
                ttk.Label(row, text=label, width=26).pack(side=tk.LEFT)
                var = tk.StringVar()
                self.vars[path] = var
                if kind == "combo":
                    widget = ttk.Combobox(
                        row, textvariable=var, values=OBJECTIVES,
                        state="readonly", width=16,
                    )
                else:
                    widget = ttk.Entry(row, textvariable=var, width=18)
                widget.pack(side=tk.RIGHT)

        self.run_btn = ttk.Button(left, text="Run Optimizer", command=self.on_run)
        self.run_btn.pack(fill=tk.X, pady=(10, 2))
        self.status = ttk.Label(left, text="Ready", foreground="gray")
        self.status.pack(fill=tk.X)

    def _build_results_panel(self):
        right = ttk.Frame(self.root, padding=10)
        right.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

        ttk.Label(
            right, text="Configurations (ranked by objective)",
            font=("", 11, "bold"),
        ).pack(anchor=tk.W)

        cols = [c[0] for c in TABLE_COLUMNS]
        self.tree = ttk.Treeview(right, columns=cols, show="headings", height=18)
        for key, heading, width in TABLE_COLUMNS:
            self.tree.heading(key, text=heading)
            anchor = tk.W if key == "motor" else tk.E
            self.tree.column(key, width=width, anchor=anchor)
        self.tree.pack(fill=tk.BOTH, expand=True, pady=6)
        self.tree.bind("<Double-1>", lambda e: self.on_show_details())

        self.details_btn = ttk.Button(
            right, text="Show data & plots for selected",
            command=self.on_show_details, state=tk.DISABLED,
        )
        self.details_btn.pack(anchor=tk.E)

    # --- config <-> form ------------------------------------------------
    def _load_defaults(self, config):
        for path, var in self.vars.items():
            section, key = path
            if key == "mass_min":
                value = config["optimizer"]["mass_bounds"][0]
            elif key == "mass_max":
                value = config["optimizer"]["mass_bounds"][1]
            else:
                value = config[section][key]
            var.set(str(value))

    def _read_config(self):
        """Build a config dict from the form; raise ValueError on bad input."""
        cfg = copy.deepcopy(CONFIG)
        for path, var in self.vars.items():
            section, key = path
            raw = var.get().strip()
            kind = self._kind_for(path)
            if kind == "combo":
                value = raw
            elif kind == "int":
                value = int(float(raw))
            else:
                value = float(raw)
            if key == "mass_min":
                lo = value
                hi = cfg["optimizer"]["mass_bounds"][1]
                cfg["optimizer"]["mass_bounds"] = (lo, hi)
            elif key == "mass_max":
                lo = cfg["optimizer"]["mass_bounds"][0]
                cfg["optimizer"]["mass_bounds"] = (lo, value)
            else:
                cfg[section][key] = value
        lo, hi = cfg["optimizer"]["mass_bounds"]
        if lo >= hi:
            raise ValueError("Mass min must be less than mass max.")
        return cfg

    def _kind_for(self, path):
        for _, fields in FIELDS:
            for p, _, kind in fields:
                if p == path:
                    return kind
        return "float"

    # --- run optimizer --------------------------------------------------
    def on_run(self):
        if self._busy:
            return
        try:
            cfg = self._read_config()
        except ValueError as exc:
            messagebox.showerror("Invalid input", str(exc))
            return
        self._set_busy(True, "Optimizing... (this runs many simulations)")
        threading.Thread(target=self._run_worker, args=(cfg,), daemon=True).start()

    def _run_worker(self, cfg):
        try:
            results = optimize(cfg)
        except Exception as exc:  # surface any modeling error to the UI thread
            self.root.after(0, self._run_failed, exc)
            return
        self.root.after(0, self._run_done, cfg, results)

    def _run_done(self, cfg, results):
        self.cfg = cfg
        self.results = results
        objective = cfg["optimizer"]["objective"]
        self.tree.delete(*self.tree.get_children())
        for i, r in enumerate(results, 1):
            m = r["metrics"]
            self.tree.insert("", tk.END, iid=str(i - 1), values=(
                i, _motor_name(r["motor_file"]), f"{r['mass']:.2f}",
                f"{m['apogee']:.1f}", f"{m['apogee_time']:.2f}",
                f"{m['max_speed']:.1f}", f"{m['max_mach']:.2f}",
                f"{m['max_acceleration']:.1f}",
            ))
        self.details_btn.config(state=tk.NORMAL if results else tk.DISABLED)
        self._set_busy(False, f"Done. {len(results)} configuration(s), ranked by {objective}.")

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
        self._set_busy(True, f"Simulating {_motor_name(result['motor_file'])}...")
        threading.Thread(
            target=self._details_worker, args=(result,), daemon=True
        ).start()

    def _details_worker(self, result):
        try:
            env, flight = run(
                self.cfg, motor_file=result["motor_file"], mass=result["mass"]
            )
            figures = generate_flight_figures(flight)
            summary = metrics(env, flight)
            os.makedirs(OUTPUT_DIR, exist_ok=True)
            export = os.path.join(
                OUTPUT_DIR,
                f"{_motor_name(result['motor_file'])}_{result['mass']:.2f}kg.csv",
            )
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
        win.title(
            f"{_motor_name(result['motor_file'])} @ {result['mass']:.2f} kg"
        )
        win.geometry("980x720")
        nb = ttk.Notebook(win)
        nb.pack(fill=tk.BOTH, expand=True)

        # Summary tab
        summary_tab = ttk.Frame(nb, padding=12)
        nb.add(summary_tab, text="Summary")
        lines = [
            f"Motor:            {_motor_name(result['motor_file'])}",
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
        tk.Label(
            summary_tab, text="\n".join(lines), justify=tk.LEFT,
            font=("Courier New", 11), anchor="nw",
        ).pack(anchor=tk.NW)

        # One tab per figure, with a pan/zoom toolbar
        for name, fig in figures:
            tab = ttk.Frame(nb)
            nb.add(tab, text=name)
            canvas = FigureCanvasTkAgg(fig, master=tab)
            canvas.draw()
            toolbar = NavigationToolbar2Tk(canvas, tab)
            toolbar.update()
            canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

    # --- helpers --------------------------------------------------------
    def _set_busy(self, busy, message):
        self._busy = busy
        self.status.config(text=message)
        state = tk.DISABLED if busy else tk.NORMAL
        self.run_btn.config(state=state)
        if not busy:
            self.details_btn.config(
                state=tk.NORMAL if self.results else tk.DISABLED
            )
        else:
            self.details_btn.config(state=tk.DISABLED)


def main():
    root = tk.Tk()
    OptimizerGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
