"""Objective-vs-mass sampling for the optimization-surface views.

The optimizer climbs the objective *score* (the exact quantity it maximizes,
see optimizer.make_scorer) as a function of airframe mass. These helpers sample
that score over a mass grid -- and, for a variable-C_d*A sweep, over a
(C_d*A, mass) grid -- so the GUI can plot the surface the optimizer traverses
and show where each optimum sits.
"""

import copy

from optimizer import evaluate, make_scorer


def _mass_grid(bounds, n):
    lo, hi = bounds
    if n <= 1:
        return [lo]
    return [lo + (hi - lo) * i / (n - 1) for i in range(n)]


def objective_vs_mass(config, motor_file, n=25):
    """Sample the objective score over the mass bounds for one motor.

    Returns (masses, scores), each a list of length ``n``.
    """
    opt = config["optimizer"]
    score = make_scorer(opt)
    masses = _mass_grid(opt["mass_bounds"], n)
    scores = [score(evaluate(config, motor_file, m), m) for m in masses]
    return masses, scores


def objective_surface(config, motor_file, cda_values, n_mass=21, progress=None):
    """Sample the objective score over a (C_d*A, mass) grid for one motor.

    ``config`` supplies everything but C_d*A, which is overridden with each
    value in ``cda_values``. Returns (cda_values, masses, Z) where ``Z`` is a
    row-per-C_d*A matrix of scores (Z[i][j] = score at cda_values[i],
    masses[j]). ``progress(done, total)`` is called after each C_d*A row.
    """
    masses = _mass_grid(config["optimizer"]["mass_bounds"], n_mass)
    Z = []
    for i, cda in enumerate(cda_values, 1):
        c = copy.deepcopy(config)
        c["rocket"]["cda"] = cda
        score = make_scorer(c["optimizer"])
        Z.append([score(evaluate(c, motor_file, m), m) for m in masses])
        if progress:
            progress(i, len(cda_values))
    return cda_values, masses, Z
