# Rocket Trajectory Optimizer

Finds, for each solid motor in a library, the **airframe mass** that maximizes a chosen
objective (max altitude, a Mach cap, or the lightest rocket that hits a target altitude),
then ranks the motors against each other. Built on [RocketPy](https://github.com/RocketPy-Team/RocketPy).

> Why mass has an optimum: a lighter rocket burns to a higher velocity but bleeds more
> energy to drag (drag ∝ v²); a heavier rocket coasts better (higher ballistic coefficient)
> but reaches a lower burnout speed. The objective-vs-mass curve is therefore unimodal.

## Install

```
python -m pip install -r requirements.txt
```

Then populate the motor library (RASP `.eng` files) from thrustcurve.org:

```
python download_motors.py                    # all Cesaroni motors
python download_motors.py --manufacturer AeroTech
python download_motors.py --impulse-class N  # only class N
```

## Run

```
python gui.py
```

The window has three columns:

- **Config** — environment, drag area (C_d·A), flight, and optimizer settings.
- **Motors** — filter the library, move motors into the *Chosen* list, add motors
  (paste or file), and save the chosen set to `data/saved`.
- **Results** — the ranked table (best airframe mass per motor). Double-click a row to
  run that configuration in full: numeric summary, every flight plot, and a CSV export.

## Objectives

| Objective | Finds the mass that… |
|---|---|
| `apogee` | flies the highest |
| `apogee_time` | stays in the air longest (to apogee) |
| `max_speed` / `max_mach` | reaches the highest speed / Mach |
| `max_acceleration` | pulls the highest acceleration |
| `apogee_capped_mach` | flies highest **without** exceeding a Mach limit |
| `min_mass_for_altitude` | is the **lightest** rocket that still reaches a target altitude |

## How it works

- **3-DOF point-mass** flight in RocketPy with the **ISA standard atmosphere** (no wind,
  no fins, no spin). Only the product C_d·A matters, so it is folded into the reference
  area with C_d = 1.
- **Motors** are RASP `.eng` files; dry and propellant mass are parsed from the header.
- Flight terminates **at apogee** (ascent only) for clean metrics and speed.
- The optimizer tunes airframe mass by **multi-start finite-difference gradient ascent**
  within `[mass_min, mass_max]`. Results that can't leave the pad or can't meet the
  chosen constraint are flagged as non-converged and ranked last.

Reported metrics: apogee (above ground), apogee time, max speed, max Mach, max acceleration.

## Project layout

| File | Role |
|---|---|
| `simulation.py` | Builds/runs the RocketPy model; `.eng` parsing, motor catalog, flight plots |
| `optimizer.py` | Scorers, gradient ascent, multi-start, per-motor sweep, ranking |
| `surface.py` | Score sampling for the 1-D / 2-D optimization-surface plots |
| `gui.py` | Tkinter UI, threading, plot embedding, CSV export |
| `store.py` | JSON persistence: settings, presets, saved configs, run history |
| `download_motors.py` | Fetches the motor library from thrustcurve.org |

Motor directories: `data/library` (full downloaded set, read-only source) and
`data/saved` (kept working set, shadows the library by name).

## Future work (V2)

- **Real weather** — swap the standard atmosphere for actual conditions (wind, temperature, pressure).
- **Interpolation** — pick any mass and drag (C_d·A) and read the predicted apogee straight off the optimization surface, without re-running the sim.
- **Better visualization** — clearer, richer plots and surface views.
- **Units & tooltips** — unit labels throughout and hover hints explaining each field.
- **Standalone app** — package as a desktop app/installer so it runs without a Python setup.

## Feedback, bugs & support

Found a bug or have feedback? Please [open an issue on GitHub](https://github.com/KnaaZzZ/rocket-trajectory/issues)
with a short description (and, for bugs, the steps that triggered it). If the tool saved
you effort, please consider supporting continued development via
[GitHub Sponsors](https://github.com/sponsors/KnaaZzZ).
