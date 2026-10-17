"""Run genuine nested EMA+ML+PPO combinatorial purged validation."""
import argparse
import json

from src.validation.real_bot_cpcv import run_cpcv


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="results/final_validation.json")
    parser.add_argument("--models", default="models/cpcv")
    parser.add_argument("--steps", type=int, default=100000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--workers", type=int, choices=(1, 2), default=2)
    arguments = parser.parse_args()
    result = run_cpcv(output=arguments.output, models=arguments.models, steps=arguments.steps,
                      seed=arguments.seed, workers=arguments.workers)
    print(json.dumps({"phase": 4, "folds": result["completed_refits"], "pbo": result["pbo"],
                      "dsr": result["dsr"], "verdict": result["verdict"]}, allow_nan=False), flush=True)


if __name__ == "__main__":
    main()
