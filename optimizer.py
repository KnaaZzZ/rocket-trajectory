"""Airframe-mass optimizer for maximum altitude.

For a fixed C_d*A (rocket radius and drag coefficients are held constant),
this sweeps every motor in data/motors and, for each one, tunes the airframe
mass by finite-difference gradient ascent to maximize apogee. The best
(motor, mass) pair overall is then reported.

Why mass has an optimum: a lighter rocket burns to a higher velocity but
bleeds more energy to drag (drag ~ v^2); a heavier rocket has a higher
ballistic coefficient (coasts better) but a lower burnout speed. The apogee
vs. mass curve is therefore unimodal, with a maximum in between.
"""

import copy

from config import CONFIG
from simulation import find_motor_files, run


def apogee_for_mass(config, motor_file, mass):
    """Run the sim with the airframe ``mass`` overridden; return apogee AGL (m)."""
    cfg = copy.deepcopy(config)
    cfg["rocket"]["mass"] = mass
    env, flight = run(cfg, motor_file=motor_file)
    return flight.apogee - env.elevation


def _clamp(value, bounds):
    lo, hi = bounds
    return max(lo, min(hi, value))


def optimize_mass(config, motor_file, opt=None):
    """Gradient-ascent on airframe mass to maximize apogee for one motor.

    Returns a dict with the best mass, apogee, evaluation count, and the
    iteration history of (mass, apogee).
    """
    opt = opt or config["optimizer"]
    bounds = opt["mass_bounds"]
    h = opt["fd_step"]

    evals = {"n": 0}

    def f(mass):
        evals["n"] += 1
        return apogee_for_mass(config, motor_file, mass)

    mass = _clamp(opt["mass_initial"], bounds)
    best = f(mass)
    history = [(mass, best)]

    for _ in range(opt["max_iter"]):
        # Central finite-difference gradient d(apogee)/d(mass), staying in bounds.
        m_hi = _clamp(mass + h, bounds)
        m_lo = _clamp(mass - h, bounds)
        grad = (f(m_hi) - f(m_lo)) / (m_hi - m_lo)

        # Proposed ascent step, capped so a large gradient can't overshoot wildly.
        step = opt["learning_rate"] * grad
        step = _clamp(step, (-opt["max_step"], opt["max_step"]))

        # Backtracking line search: shrink the step until apogee improves.
        improved = False
        while abs(step) >= opt["tol"]:
            candidate = _clamp(mass + step, bounds)
            value = f(candidate)
            if value > best:
                mass, best = candidate, value
                history.append((mass, best))
                improved = True
                break
            step *= 0.5

        if not improved:  # converged: no improving step found
            break

    return {
        "motor_file": motor_file,
        "mass": mass,
        "apogee": best,
        "evaluations": evals["n"],
        "history": history,
    }


def optimize(config=None):
    """Optimize airframe mass for every motor; return per-motor and best results."""
    config = config or CONFIG
    motor_files = find_motor_files()
    if not motor_files:
        raise FileNotFoundError("No .eng motor files found in data/motors")

    results = []
    for motor_file in motor_files:
        result = optimize_mass(config, motor_file)
        results.append(result)

    best = max(results, key=lambda r: r["apogee"])
    return {"results": results, "best": best}


def _motor_name(path):
    import os

    return os.path.splitext(os.path.basename(path))[0]


if __name__ == "__main__":
    out = optimize(CONFIG)

    print(f"{'Motor':<32} {'Opt. mass (kg)':>14} {'Apogee AGL (m)':>15} {'evals':>7}")
    print("-" * 72)
    for r in out["results"]:
        print(
            f"{_motor_name(r['motor_file']):<32} "
            f"{r['mass']:>14.2f} {r['apogee']:>15.1f} {r['evaluations']:>7}"
        )

    best = out["best"]
    print("-" * 72)
    print(
        f"BEST: {_motor_name(best['motor_file'])} @ {best['mass']:.2f} kg "
        f"-> {best['apogee']:.1f} m"
    )
