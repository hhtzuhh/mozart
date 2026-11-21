"""Compare two PianoMime rollouts against the same target and decide whether to adopt the change."""

import argparse
import json
from pathlib import Path

import numpy as np


def load_trace(path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray, int]:
    with np.load(path, allow_pickle=False) as trace:
        target = trace["target_keys"]
        played = trace["played_keys"]
        actions = trace["actions"]
        seed = int(trace["seed"].item())

    if target.ndim != 2 or target.shape[1] != 88 or not len(target):
        raise ValueError(f"Expected a nonempty T by 88 key trace in {path}")
    if played.shape != target.shape or played.dtype != bool or target.dtype != bool:
        raise ValueError(f"Target and played keys must be equally shaped Boolean arrays in {path}")
    if actions.shape != (len(target), 47) or not np.isfinite(actions).all():
        raise ValueError(f"Expected a finite T by 47 action trace in {path}")
    return target, played, actions, seed


def score(target: np.ndarray, played: np.ndarray, start: int, stop: int) -> dict:
    hits = (target & played).sum(axis=1)
    misses = (target & ~played).sum(axis=1)
    extras = (~target & played).sum(axis=1)
    denominator = 2 * hits + misses + extras
    # This matches PianoMime's frame-wise binary F1 with zero_division=1.
    frame_f1 = np.divide(2 * hits, denominator, out=np.ones(len(target)), where=denominator != 0)
    active = target.any(axis=1)
    return {
        "f1": float(frame_f1.mean()),
        "target_active_frame_f1": float(frame_f1[active].mean()) if active.any() else None,
        "window_f1": float(frame_f1[start:stop].mean()),
        "window_missed_keys": int(misses[start:stop].sum()),
        "window_extra_keys": int(extras[start:stop].sum()),
    }


def compare(baseline_path: Path, candidate_path: Path, start: int, stop: int) -> dict:
    target, baseline_keys, baseline_actions, seed = load_trace(baseline_path)
    candidate_target, candidate_keys, candidate_actions, candidate_seed = load_trace(candidate_path)
    if seed != candidate_seed or not np.array_equal(target, candidate_target):
        raise ValueError("Both rollouts must use the same seed and target keys")
    if not 0 <= start < stop <= len(target):
        raise ValueError("The analysis window must be within the rollout")

    baseline = score(target, baseline_keys, start, stop)
    candidate = score(target, candidate_keys, start, stop)
    delta = candidate["f1"] - baseline["f1"]
    return {
        "seed": seed,
        "frames": len(target),
        "start_frame": start,
        "stop_frame_exclusive": stop,
        "baseline": baseline,
        "candidate": candidate,
        "f1_delta": delta,
        "changed_key_frames": int(np.any(baseline_keys != candidate_keys, axis=1).sum()),
        "changed_action_frames": int(np.any(baseline_actions != candidate_actions, axis=1).sum()),
        "adopt_candidate": delta > 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--start-frame", type=int, required=True)
    parser.add_argument("--stop-frame", type=int, required=True)
    args = parser.parse_args()
    try:
        result = compare(args.baseline, args.candidate, args.start_frame, args.stop_frame)
    except (OSError, KeyError, ValueError) as error:
        parser.error(str(error))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
