"""Airframe-mass optimizer for maximum altitude.

For a fixed C_d*A (drag coefficient and reference area held constant), this
sweeps every motor in data/motors and, for each one, tunes the airframe mass
by finite-difference gradient ascent to maximize the objective metric. It then
lists every configuration, ranked by that objective, with the full flight
metrics (apogee, apogee time, max speed, max Mach, max acceleration).

Why mass has an optimum: a lighter rocket burns to a higher velocity but
bleeds more energy to drag (drag ~ v^2); a heavier rocket has a higher
ballistic coefficient (coasts better) but a lower burnout speed. The objective
vs. mass curve is therefore unimodal, with a maximum in between.
"""

import os

from config import CONFIG
from simulation import find_motor_files, metrics, run


def evaluate(config, motor_file, mass):
    """Run the sim at the given airframe ``mass``; return the metrics dict."""
    env, flight = run(config, motor_file=motor_file, mass=mass)
    return metrics(env, flight)


# Objectives that are just a raw flight metric (maximized directly). The last
# four are monotonic in mass, so on their own they drive mass to the lower
# bound; they are mainly useful as reported columns.
_METRIC_OBJECTIVES = (
    "apogee",
    "apogee_time",
    "max_speed",
    "max_mach",
    "max_acceleration",
)


def make_scorer(opt):
    """Return score(metrics, mass) -> float that the optimizer maximizes.

    Handles the raw-metric objectives plus two derived ones:

    * ``apogee_capped_mach``  -- maximize apogee subject to max_mach <= mach_limit,
      enforced with a quadratic penalty on any exceedance.
    * ``min_mass_for_altitude`` -- minimize airframe mass subject to
      apogee >= target_altitude (maximize -mass, penalizing any shortfall).
    """
    objective = opt["objective"]

    if objective in _METRIC_OBJECTIVES:
        return lambda m, mass: m[objective]

    if objective == "apogee_capped_mach":
        limit = opt["mach_limit"]
        penalty = 1.0e6
        return lambda m, mass: m["apogee"] - penalty * max(0.0, m["max_mach"] - limit) ** 2

    if objective == "min_mass_for_altitude":
        target = opt["target_altitude"]
        penalty = 100.0
        return lambda m, mass: -mass - penalty * max(0.0, target - m["apogee"]) ** 2

    raise ValueError(f"Unknown objective {objective!r}")


def _clamp(value, bounds):
    lo, hi = bounds
    return max(lo, min(hi, value))


def optimize_mass(config, motor_file, opt=None):
    """Gradient-ascent on airframe mass to maximize the objective for one motor.

    Uses a central finite-difference gradient for the ascent direction and a
    full ``max_step`` trial with backtracking for the magnitude. The trial size
    is deliberately independent of the gradient magnitude, which varies by
    orders of magnitude across objectives (hundreds of m/kg for apogee, ~1 for
    min-mass). Returns the motor file, best mass, full metrics, and eval count.
    """
    opt = opt or config["optimizer"]
    score = make_scorer(opt)
    bounds = opt["mass_bounds"]
    h = opt["fd_step"]
    max_step = opt["max_step"]
    tol = opt["tol"]

    evals = {"n": 0}

    def f(mass):
        evals["n"] += 1
        return score(evaluate(config, motor_file, mass), mass)

    mass = _clamp(opt["mass_initial"], bounds)
    best = f(mass)

    for _ in range(opt["max_iter"]):
        # Central finite-difference gradient of the score w.r.t. mass, in bounds.
        m_hi = _clamp(mass + h, bounds)
        m_lo = _clamp(mass - h, bounds)
        if m_hi == m_lo:
            break
        grad = (f(m_hi) - f(m_lo)) / (m_hi - m_lo)
        if grad == 0.0:
            break

        # Full step in the ascent direction; backtracking shrinks it to an
        # improving, in-bounds move (or gives up when below tolerance).
        step = max_step if grad > 0 else -max_step
        improved = False
        while abs(step) >= tol:
            candidate = _clamp(mass + step, bounds)
            if candidate != mass:
                value = f(candidate)
                if value > best:
                    mass, best = candidate, value
                    improved = True
                    break
            step *= 0.5

        if not improved:  # converged: no improving step found
            break

    return {
        "motor_file": motor_file,
        "mass": mass,
        "score": best,
        "metrics": evaluate(config, motor_file, mass),
        "evaluations": evals["n"] + 1,
    }


def optimize(config=None):
    """Optimize airframe mass for every motor; return configs ranked by score."""
    config = config or CONFIG

    motor_files = find_motor_files()
    if not motor_files:
        raise FileNotFoundError("No .eng motor files found in data/motors")

    results = [optimize_mass(config, mf) for mf in motor_files]
    results.sort(key=lambda r: r["score"], reverse=True)
    return results


def _motor_name(path):
    return os.path.splitext(os.path.basename(path))[0]


def report(results, objective="apogee"):
    """Print all configurations as a table, ranked by the objective."""
    header = (
        f"{'#':>2}  {'Motor':<28} {'Mass':>7} {'Apogee':>10} {'Apo t':>7} "
        f"{'MaxV':>8} {'MaxMach':>8} {'MaxAcc':>9}"
    )
    units = (
        f"{'':>2}  {'':<28} {'(kg)':>7} {'(m AGL)':>10} {'(s)':>7} "
        f"{'(m/s)':>8} {'(-)':>8} {'(m/s^2)':>9}"
    )
    print(f"Ranked by: {objective} (highest first)")
    print(header)
    print(units)
    print("-" * len(header))
    for i, r in enumerate(results, 1):
        m = r["metrics"]
        print(
            f"{i:>2}  {_motor_name(r['motor_file']):<28} {r['mass']:>7.2f} "
            f"{m['apogee']:>10.1f} {m['apogee_time']:>7.2f} "
            f"{m['max_speed']:>8.1f} {m['max_mach']:>8.2f} {m['max_acceleration']:>9.1f}"
        )


if __name__ == "__main__":
    results = optimize(CONFIG)
    report(results, CONFIG["optimizer"]["objective"])
