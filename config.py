"""Simulation configuration.

Edit everything here. A future GUI can build/replace this same dict structure.
"""

CONFIG = {
    # --- Launch site & atmosphere ---------------------------------------
    "environment": {
        "latitude": 39.4025,        # deg (39 24 09 N)
        "longitude": -8.289167,     # deg (008 17 21 W)
        "elevation": 164.897,           # m, above sea level
        "atmospheric_model": "standard_atmosphere",
    },

    # --- Motor ----------------------------------------------------------
    # The motor is loaded automatically from the .eng file(s) in
    # data/motors (see MOTORS_DIR in simulation.py). Dry mass and
    # propellant mass are read from the .eng header.

    # --- Rocket (point mass) --------------------------------------------
    "rocket": {
        "radius": 0.049,                     # m, reference radius -> drag area = pi*r^2
        "mass": 20.0,                        # kg, airframe without motor
        "center_of_mass_without_motor": 0.0, # m
        "power_off_drag": 0.5,               # Cd, constant or function of Mach
        "power_on_drag": 0.5,                # Cd, constant or function of Mach
        "motor_position": 0.0,               # m, motor mount position
    },

    # --- Flight / launch conditions -------------------------------------
    "flight": {
        "rail_length": 5.0,      # m
        "inclination": 85.0,     # deg from horizontal
        "heading": 0.0,          # deg from north
        "simulation_mode": "3 DOF",  # required for point-mass models
    },

    # --- Optimizer ------------------------------------------------------
    # Sweeps every motor in data/motors and, for each, tunes the airframe
    # mass by gradient ascent to maximize the objective. C_d*A is held
    # constant (rocket radius + drag coefficients are fixed).
    "optimizer": {
        "objective": "apogee",     # currently: maximize apogee (AGL)
        "mass_initial": 20.0,      # kg, starting airframe mass guess
        "mass_bounds": (1.0, 100.0),  # kg, (min, max) airframe mass
        "fd_step": 0.25,           # kg, finite-difference step for the gradient
        "learning_rate": 0.5,      # kg per (m/kg) of gradient, initial step scale
        "max_step": 10.0,          # kg, cap on a single update step
        "tol": 1e-2,               # kg, stop when the mass update is smaller than this
        "max_iter": 40,            # safety cap on gradient-ascent iterations
    },
}
