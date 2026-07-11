"""Simulation configuration.

Edit everything here. A future GUI can build/replace this same dict structure.
"""

CONFIG = {
    # --- Launch site ----------------------------------------------------
    # Atmosphere is always the ISA standard atmosphere (set in simulation.py).
    "environment": {
        "latitude": 39.4025,        # deg (39 24 09 N)
        "longitude": -8.289167,     # deg (008 17 21 W)
        "elevation": 164.897,       # m, above sea level
    },

    # --- Motor ----------------------------------------------------------
    # Loaded automatically from the .eng file(s) in the motor library (data/library)
    # (see MOTORS_DIR in simulation.py). Dry mass and propellant mass are
    # read from the .eng header.

    # --- Rocket (point mass) --------------------------------------------
    # Drag force = 0.5 * rho * v^2 * drag * reference_area  (constant C_d*A).
    # Airframe mass is not set here -- it is the optimizer's design variable.
    "rocket": {
        "drag": 0.5,               # C_d, constant (a function of Mach is also allowed)
        "reference_area": 0.007543,  # m^2 (e.g. pi * 0.049^2 for a 98 mm body)
    },

    # --- Flight / launch conditions -------------------------------------
    # Always runs in 3-DOF point-mass mode (set in simulation.py).
    "flight": {
        "rail_length": 5.0,      # m, launch rail length (minor effect near vertical)
        "inclination": 85.0,     # deg from horizontal (90 = straight up)
        "heading": 0.0,          # deg azimuth (0 = North, 90 = East); inert without wind
    },

    # --- Optimizer ------------------------------------------------------
    # Sweeps every motor in the motor library (data/library) and, for each, tunes the airframe
    # mass by gradient ascent to maximize the objective. C_d*A is constant.
    "optimizer": {
        # Available objectives:
        #   apogee               - maximize peak altitude (AGL)
        #   apogee_time          - maximize time to apogee    (-> lower bound mass)
        #   max_speed            - maximize peak speed         (-> lower bound mass)
        #   max_mach             - maximize peak Mach          (-> lower bound mass)
        #   max_acceleration     - maximize peak acceleration  (-> lower bound mass)
        #   apogee_capped_mach   - maximize apogee s.t. max_mach <= mach_limit
        #   min_mass_for_altitude- minimize mass s.t. apogee >= target_altitude
        "objective": "apogee",
        "mach_limit": 3.0,          # used by objective "apogee_capped_mach"
        "target_altitude": 12000.0, # m AGL, used by objective "min_mass_for_altitude"
        "mass_initial": 20.0,      # kg, starting airframe mass guess
        "mass_bounds": (1.0, 100.0),  # kg, (min, max) airframe mass
        "fd_step": 0.25,           # kg, finite-difference step for the gradient
        "max_step": 10.0,          # kg, full trial step (backtracking shrinks it)
        "tol": 1e-3,               # kg, stop when the mass update is smaller than this
        "max_iter": 40,            # safety cap on gradient-ascent iterations
    },
}
