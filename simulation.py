"""Point-mass trajectory simulation using RocketPy.

Builds and runs the model from a config dict (see config.py). The motor is
loaded automatically from the .eng file(s) in MOTORS_DIR -- its dry mass and
propellant mass are parsed from the .eng header.
"""

import glob
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


def run(config, motor_file=None):
    """Build the model from a config dict and return (environment, flight).

    If ``motor_file`` is None, the first .eng file found in MOTORS_DIR is used.
    """
    if motor_file is None:
        motor_files = find_motor_files()
        if not motor_files:
            raise FileNotFoundError(f"No .eng motor files found in {MOTORS_DIR!r}")
        motor_file = motor_files[0]

    env = Environment(
        latitude=config["environment"]["latitude"],
        longitude=config["environment"]["longitude"],
        elevation=config["environment"]["elevation"],
    )
    env.set_atmospheric_model(type=config["environment"]["atmospheric_model"])

    motor = load_point_mass_motor(motor_file)

    rocket_cfg = dict(config["rocket"])  # copy so we can pop non-constructor keys
    motor_position = rocket_cfg.pop("motor_position")
    rocket = PointMassRocket(**rocket_cfg)
    rocket.add_motor(motor, position=motor_position)

    flight = Flight(rocket=rocket, environment=env, **config["flight"])
    return env, flight


def summarize(env, flight):
    """Print the key flight results."""
    print(f"Apogee (AGL):     {flight.apogee - env.elevation:8.1f} m")
    print(f"Apogee time:      {flight.apogee_time:8.2f} s")
    print(f"Max speed:        {flight.max_speed:8.1f} m/s")
    print(f"Max Mach:         {flight.max_mach_number:8.2f}")
    print(f"Max acceleration: {flight.max_acceleration:8.1f} m/s^2")
