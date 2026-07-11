"""Entry point: load the config, run the simulation, print results."""

from config import CONFIG
from simulation import run, summarize

if __name__ == "__main__":
    env, flight = run(CONFIG)
    summarize(env, flight)
