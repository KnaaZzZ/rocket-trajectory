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


def is_converged(config, result):
    """Whether an optimized result is a usable solution.

    Invalid ("did not converge") when the rocket never really flies, or when the
    objective's constraint can't be met within the mass bounds:

    * apogee <= 0 -- thrust can't lift the airframe (never leaves the pad).
    * min_mass_for_altitude -- the achieved apogee misses the target by more
      than 5% (too weak to reach it, or too strong so it overshoots even at the
      lightest allowed mass).
    * apogee_capped_mach -- the Mach cap is still exceeded (by >2%) even at the
      optimized mass.
    """
    opt = config["optimizer"]
    m = result["metrics"]
    if m["apogee"] <= 0:
        return False
    objective = opt["objective"]
    if objective == "min_mass_for_altitude":
        target = opt.get("target_altitude")
        if target and target > 0 and not (0.95 * target <= m["apogee"] <= 1.05 * target):
            return False
    elif objective == "apogee_capped_mach":
        limit = opt.get("mach_limit")
        if limit and m["max_mach"] > limit * 1.02:
            return False
    return True


def optimize(config, motor_files=None, progress=None):
    """Optimize airframe mass for each motor; return configs ranked by score.

    ``config`` is the full parameter dict (built by the GUI from its inputs).
    ``motor_files`` is the list of .eng paths to sweep; if None, the whole
    library is used. ``progress`` is an optional callback(done, total, name)
    invoked after each motor, e.g. to drive a GUI progress bar.

    Each result carries a ``converged`` flag (see is_converged). Converged
    results are ranked first (by score); non-converged ones follow.
    """
    if motor_files is None:
        motor_files = find_motor_files()
    if not motor_files:
        raise FileNotFoundError("No .eng motor files to optimize.")

    results = []
    for i, mf in enumerate(motor_files, 1):
        result = optimize_mass(config, mf)
        result["converged"] = is_converged(config, result)
        results.append(result)
        if progress:
            progress(i, len(motor_files), _motor_name(mf))
    # Converged first, then by score (both descending).
    results.sort(key=lambda r: (r["converged"], r["score"]), reverse=True)
    return results


def _motor_name(path):
    return os.path.splitext(os.path.basename(path))[0]
