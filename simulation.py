"""Point-mass trajectory simulation using RocketPy.

Builds and runs the model from a config dict (see config.py). The atmosphere
(ISA standard) and 3-DOF point-mass mode are fixed here, not in the config.
The motor is loaded from the .eng file(s) in MOTORS_DIR, with its dry mass and
propellant mass parsed from the .eng header.
"""

import glob
import math
import os

from rocketpy import Environment, Flight, PointMassMotor, PointMassRocket

# Directory scanned for motor thrust-curve files.
MOTORS_DIR = "data/motors"


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


# Flight plots relevant to a point-mass model. The rest of RocketPy's plots
# (attitude, angular kinematics, stability/control, rail buttons, pressure
# sensors) need rotation, aero surfaces, or parachutes and don't apply here.
_POINT_MASS_PLOTS = (
    "trajectory_3d",
    "linear_kinematics_data",   # position, velocity, acceleration vs time
    "flight_path_angle_data",
    "fluid_mechanics_data",     # Mach, Reynolds, dynamic pressure vs time
    "energy_data",              # kinetic / potential / total energy
)


def show_all_plots(flight, plot_names=_POINT_MASS_PLOTS):
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
