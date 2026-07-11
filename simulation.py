"""Point-mass trajectory simulation using RocketPy.

Builds and runs the model from a config dict (see config.py). The atmosphere
(ISA standard) and 3-DOF point-mass mode are fixed here, not in the config.
Motors are loaded from .eng file(s), with dry mass and propellant mass parsed
from the .eng header.

Motor directories:
  * LIBRARY_DIR -- the full downloaded motor library (read-only source).
  * SAVED_DIR   -- motors the user has chosen/added and saved to keep.
"""

import glob
import math
import os
import shutil

from rocketpy import Environment, Flight, PointMassMotor, PointMassRocket

# Full downloaded motor library (source) and the user's saved working set.
LIBRARY_DIR = "data/library"
SAVED_DIR = "data/saved"
MOTORS_DIR = LIBRARY_DIR  # default directory the optimizer/run() fall back to


def parse_eng_header(eng_path):
    """Read masses from a RASP .eng header line.

    The first non-comment line has the form::

        name  diameter(mm)  length(mm)  delays  prop_mass(kg)  total_mass(kg)  mfr

    Returns a dict with ``propellant_initial_mass`` and ``dry_mass`` (kg),
    where dry mass = total (loaded) mass - propellant mass.
    """
    with open(eng_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith(";"):
                continue  # skip blanks and comment lines
            fields = line.split()
            propellant_mass = float(fields[4])
            total_mass = float(fields[5])
            return {
                "propellant_initial_mass": propellant_mass,
                "dry_mass": total_mass - propellant_mass,
            }
    raise ValueError(f"No motor header line found in {eng_path!r}")


def find_motor_files(directory=MOTORS_DIR):
    """Return a sorted list of .eng motor files in ``directory``."""
    return sorted(glob.glob(os.path.join(directory, "*.eng")))


def load_point_mass_motor(eng_path):
    """Build a PointMassMotor from a single .eng file."""
    masses = parse_eng_header(eng_path)
    return PointMassMotor(thrust_source=eng_path, **masses)


def motor_name(eng_path):
    """Human-readable motor name from an .eng path (no dir, no extension)."""
    return os.path.splitext(os.path.basename(eng_path))[0]


def validate_eng_text(text):
    """Raise ValueError if ``text`` isn't a usable RASP .eng motor definition.

    Checks the header parses to positive masses and that at least one thrust
    data point is present.
    """
    header_seen = False
    data_points = 0
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(";"):
            continue
        if not header_seen:
            fields = line.split()
            if len(fields) < 7:
                raise ValueError("Header line must have 7 fields "
                                 "(name diam length delays prop_mass total_mass mfr).")
            prop, total = float(fields[4]), float(fields[5])
            if prop <= 0 or total <= prop:
                raise ValueError("Propellant mass must be > 0 and less than total mass.")
            header_seen = True
        else:
            parts = line.split()
            if len(parts) >= 2:
                float(parts[0]); float(parts[1])
                data_points += 1
    if not header_seen:
        raise ValueError("No header line found.")
    if data_points < 2:
        raise ValueError("Need at least two thrust-curve data points.")


def save_motor_text(text, name, dest_dir=SAVED_DIR):
    """Validate ``text`` and write it as ``dest_dir/<name>.eng``. Returns the path."""
    validate_eng_text(text)
    os.makedirs(dest_dir, exist_ok=True)
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in name).strip("_")
    if not safe:
        safe = "motor"
    if not safe.lower().endswith(".eng"):
        safe += ".eng"
    path = os.path.join(dest_dir, safe)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text if text.endswith("\n") else text + "\n")
    return path


def save_motor_files(eng_paths, dest_dir=SAVED_DIR):
    """Copy the given .eng files into ``dest_dir``. Returns the new paths."""
    os.makedirs(dest_dir, exist_ok=True)
    saved = []
    for src in eng_paths:
        dest = os.path.join(dest_dir, os.path.basename(src))
        if os.path.abspath(src) != os.path.abspath(dest):
            shutil.copyfile(src, dest)
        saved.append(dest)
    return saved


def run(config, motor_file=None, mass=None):
    """Build the model from a config dict and return (environment, flight).

    ``motor_file`` defaults to the first .eng file in MOTORS_DIR.
    ``mass`` is the airframe mass (kg); it is the optimizer's design variable,
    so it is passed in here rather than read from the config. If None, the
    optimizer's ``mass_initial`` is used.
    """
    if motor_file is None:
        motor_files = find_motor_files()
        if not motor_files:
            raise FileNotFoundError(f"No .eng motor files found in {MOTORS_DIR!r}")
        motor_file = motor_files[0]
    if mass is None:
        mass = config["optimizer"]["mass_initial"]

    env = Environment(
        latitude=config["environment"]["latitude"],
        longitude=config["environment"]["longitude"],
        elevation=config["environment"]["elevation"],
    )
    env.set_atmospheric_model(type="standard_atmosphere")

    motor = load_point_mass_motor(motor_file)

    # Constant C_d*A: convert reference area to the radius RocketPy expects,
    # and use the same drag coefficient with the motor on and off.
    rocket_cfg = config["rocket"]
    radius = math.sqrt(rocket_cfg["reference_area"] / math.pi)
    rocket = PointMassRocket(
        radius=radius,
        mass=mass,
        center_of_mass_without_motor=0.0,  # irrelevant for a 3-DOF point mass
        power_off_drag=rocket_cfg["drag"],
        power_on_drag=rocket_cfg["drag"],
    )
    rocket.add_motor(motor, position=0.0)  # position irrelevant for a point mass

    flight = Flight(
        rocket=rocket,
        environment=env,
        rail_length=config["flight"]["rail_length"],
        inclination=config["flight"]["inclination"],
        heading=config["flight"]["heading"],
        simulation_mode="3 DOF",  # required for point-mass models
    )
    return env, flight


def metrics(env, flight):
    """Return the key flight results as a dict."""
    return {
        "apogee": flight.apogee - env.elevation,   # m, above ground level
        "apogee_time": flight.apogee_time,         # s
        "max_speed": flight.max_speed,             # m/s
        "max_mach": flight.max_mach_number,        # -
        "max_acceleration": flight.max_acceleration,  # m/s^2
    }


# Every RocketPy flight plot. Comment out any you don't want -- ones that
# don't apply to a point-mass flight (rotation, aero surfaces, parachutes) are
# skipped automatically with a printed note.
_FLIGHT_PLOTS = (
    "trajectory_3d",
    "linear_kinematics_data",       # position, velocity, acceleration vs time
    "flight_path_angle_data",
    "aerodynamic_forces",
    "fluid_mechanics_data",         # Mach, Reynolds, dynamic pressure vs time
    "energy_data",                  # kinetic / potential / total energy
    "pressure_rocket_altitude",
    "pressure_signals",
    "rail_buttons_forces",
    "rail_buttons_bending_moments",
)


def generate_flight_figures(flight, plot_names=_FLIGHT_PLOTS):
    """Build the selected flight plots and return them as (name, Figure) pairs.

    Like show_all_plots but returns the matplotlib Figures instead of displaying
    them, so a GUI can embed them. Inapplicable plots are skipped.
    """
    import matplotlib.pyplot as plt

    real_show = plt.show
    plt.show = lambda *args, **kwargs: None
    figures = []
    try:
        for name in plot_names:
            existing = set(plt.get_fignums())
            try:
                getattr(flight.plots, name)()
            except Exception as exc:
                print(f"  (skipped {name}: {exc})")
                continue
            for num in plt.get_fignums():
                if num not in existing:
                    figures.append((name, plt.figure(num)))
    finally:
        plt.show = real_show
    return figures


def show_all_plots(flight, plot_names=_FLIGHT_PLOTS):
    """Draw the selected flight plots and show them all at once.

    RocketPy calls plt.show() after each plot, which makes figures appear one
    at a time. We suppress those intermediate calls so every figure is built
    first, then show them together in a single blocking call.
    """
    import matplotlib.pyplot as plt

    real_show = plt.show
    plt.show = lambda *args, **kwargs: None  # collect figures, don't display yet
    try:
        for name in plot_names:
            try:
                getattr(flight.plots, name)()
            except Exception as exc:  # a plot may not apply to this flight
                print(f"  (skipped {name}: {exc})")
    finally:
        plt.show = real_show

    plt.show()  # display every accumulated figure simultaneously


def full_details(config, motor_file, mass, export_path=None):
    """Re-run one configuration and emit its data and the relevant curves.

    Prints the numeric flight summary, shows the point-mass-relevant plots all
    at once, and (if ``export_path`` is given) exports the flight time series to
    CSV. Returns (environment, flight).
    """
    env, flight = run(config, motor_file=motor_file, mass=mass)

    flight.info()          # numeric summary (text only, no plots)
    show_all_plots(flight)  # relevant curves, shown together

    if export_path:
        os.makedirs(os.path.dirname(export_path) or ".", exist_ok=True)
        flight.export_data(export_path)
        print(f"\nFlight time series exported to {export_path}")

    return env, flight
