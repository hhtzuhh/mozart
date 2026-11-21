"""Run PianoMime's low-level evaluator and record the inputs to its stock score."""

import argparse
import random
import runpy
import sys
from pathlib import Path

import numpy as np
import torch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--trace", type=Path, required=True)
    args = parser.parse_args()

    repo = Path("pianomime").resolve()
    script = repo / "multi_task" / "eval_low_level.py"
    sys.path[:0] = [str(script.parent), str(repo)]
    from robopianist.wrappers import evaluation

    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)

    target_keys = []
    played_keys = []
    actions = []
    stock_metric = evaluation.precision_recall_fscore_support
    stock_step = evaluation.MidiEvaluationWrapper.step

    def record_metric(y_true, y_pred, **kwargs):
        if np.shape(y_true) == (88,):
            target_keys.append(np.asarray(y_true, dtype=bool).copy())
            played_keys.append(np.asarray(y_pred, dtype=bool).copy())
        return stock_metric(y_true, y_pred, **kwargs)

    def record_step(wrapper, action):
        actions.append(np.asarray(action).copy())
        return stock_step(wrapper, action)

    evaluation.precision_recall_fscore_support = record_metric
    evaluation.MidiEvaluationWrapper.step = record_step
    sys.argv = [str(script), args.task]
    runpy.run_path(str(script), run_name="__main__")

    if not (len(actions) == len(target_keys) == len(played_keys) > 0):
        raise RuntimeError("The evaluator did not produce a complete key and action trace")
    np.savez_compressed(
        args.trace,
        target_keys=np.stack(target_keys),
        played_keys=np.stack(played_keys),
        actions=np.stack(actions),
        seed=args.seed,
    )


if __name__ == "__main__":
    main()
