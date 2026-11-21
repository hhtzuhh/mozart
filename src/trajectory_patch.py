"""Write a PianoMime hand-trajectory candidate with one local movement edit."""

import argparse
import hashlib
import json
from pathlib import Path
import tempfile

import numpy as np


HANDS = ("left", "right")
WHITE_KEY_PITCH_M = 0.0235
LATERAL_PITCHES = {
    "higher_half_key": 0.5, "higher_one_key": 1.0,
    "lower_half_key": -0.5, "lower_one_key": -1.0,
}
PREPARATION_OFFSETS = {"prepare_earlier": -1, "prepare_later": 1}
HAND_MOTIONS = (*LATERAL_PITCHES, *PREPARATION_OFFSETS)
TIP_SHIFTS = {
    f"tip_{tip}_{direction}": (tip, sign * WHITE_KEY_PITCH_M / 2)
    for tip in range(1, 6)
    for direction, sign in (("higher", 1), ("lower", -1))
}
ADAPTIVE_MOTIONS = (*HAND_MOTIONS, *TIP_SHIFTS)


def move_hand(trajectory: np.ndarray, start: int, stop: int, motion: str) -> np.ndarray:
    if not 0 <= start < stop <= len(trajectory) or stop - start < 2:
        raise ValueError("Select at least two frames within the trajectory")
    result = trajectory.copy()
    if motion in LATERAL_PITCHES or motion in TIP_SHIFTS:
        length = stop - start
        ramp = np.minimum(np.arange(1, length + 1), np.arange(length, 0, -1))
        weight = np.minimum(ramp / 4, 1)
        if motion in TIP_SHIFTS:
            tip, distance = TIP_SHIFTS[motion]
            result[start:stop, 1, tip] += weight * distance
        else:
            offsets = weight * (WHITE_KEY_PITCH_M * LATERAL_PITCHES[motion])
            result[start:stop, 1, :] += offsets[:, None]
    elif motion in PREPARATION_OFFSETS:
        indices = np.clip(np.arange(start, stop) - PREPARATION_OFFSETS[motion], start, stop - 1)
        result[start:stop, :2, :] = trajectory[indices, :2, :]
    else:
        raise ValueError(f"Unknown hand movement: {motion}")
    return result


def trajectory_name(task: str, hand: str) -> str:
    if not task or Path(task).name != task or task in (".", ".."):
        raise ValueError("Task must be a single filename component")
    return f"{task}_{hand}_hand_action_list.npy"


def shift_window(trajectory: np.ndarray, start: int, stop: int, offset: int) -> np.ndarray:
    if not 0 <= start < stop <= len(trajectory) or stop - start < 2:
        raise ValueError("Select at least two frames within the trajectory")
    if offset not in (-1, 1):
        raise ValueError("The supported timing change is one frame earlier or later")

    result = trajectory.copy()
    indices = np.arange(start, stop) - offset
    result[start:stop] = trajectory[np.clip(indices, start, stop - 1)]
    return result


def write_candidate(
    source: Path,
    destination: Path,
    task: str,
    hand: str,
    start: int,
    stop: int,
    offset: int | None = None,
    *,
    motion: str | None = None,
) -> dict:
    if (offset is None) == (motion is None):
        raise ValueError("Specify one timing offset or one hand movement")
    operation = {"offset_frames": offset} if motion is None else {"motion": motion}
    return write_edits(source, destination, task, [
        {"hand": hand, "start_frame": start, "stop_frame": stop, **operation}])


def write_edits(source: Path, destination: Path, task: str, edits: list[dict]) -> dict:
    """Apply disjoint edits to a single parent and publish both hand files together."""
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"Candidate directory already exists: {destination}")
    intervals = sorted((edit["start_frame"], edit["stop_frame"]) for edit in edits)
    if not intervals or any(a < prev_stop for (_, prev_stop), (a, _) in zip(intervals, intervals[1:])):
        raise ValueError("Select at least one edit and use non-overlapping passages")
    paths = {side: source / trajectory_name(task, side) for side in HANDS}
    arrays = {side: np.load(path, allow_pickle=False) for side, path in paths.items()}
    for side, array in arrays.items():
        if array.ndim != 3 or array.shape[1:] != (3, 6) or not len(array):
            raise ValueError(f"Invalid {side} trajectory shape: {array.shape}")
        if not np.issubdtype(array.dtype, np.floating) or not np.isfinite(array).all():
            raise ValueError(f"The {side} trajectory must contain finite float values")
    if arrays["left"].shape != arrays["right"].shape:
        raise ValueError("Both hands must have the same number of frames")

    changes = []
    for edit in edits:
        hand, start, stop = (edit[k] for k in ("hand", "start_frame", "stop_frame"))
        if hand not in HANDS or ("motion" in edit) == ("offset_frames" in edit):
            raise ValueError("Each edit needs one hand and one movement")
        original = arrays[hand]
        if "motion" in edit:
            operation = {"motion": edit["motion"]}
            arrays[hand] = move_hand(original, start, stop, edit["motion"])
        else:
            operation = {"offset_frames": edit["offset_frames"]}
            arrays[hand] = shift_window(original, start, stop, edit["offset_frames"])
        changed_frames = int(np.any(arrays[hand] != original, axis=(1, 2)).sum())
        if not changed_frames:
            raise ValueError("The selected movement leaves every frame identical")
        changes.append({"hand": hand, "start_frame": start, "stop_frame_exclusive": stop,
                        "changed_frames": changed_frames, **operation})

    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as staging:
        candidate = Path(staging) / "candidate"
        candidate.mkdir()
        checksums = {}
        for side, array in arrays.items():
            output_path = candidate / paths[side].name
            np.save(output_path, array, allow_pickle=False)
            checksums[side] = {
                "input_sha256": hashlib.sha256(paths[side].read_bytes()).hexdigest(),
                "output_sha256": hashlib.sha256(output_path.read_bytes()).hexdigest(),
            }
        details = changes[0] if len(changes) == 1 else {"edits": changes}
        decision = {"task": task, **details, "trajectories": checksums}
        (candidate / "modification.json").write_text(json.dumps(decision, indent=2) + "\n")
        candidate.rename(destination)

    return decision


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--hand", choices=HANDS, required=True)
    parser.add_argument("--start-frame", type=int, required=True)
    parser.add_argument("--stop-frame", type=int, required=True, help="Exclusive end frame")
    edit = parser.add_mutually_exclusive_group(required=True)
    edit.add_argument("--offset", type=int, choices=(-1, 1),
                      help="Shift the entire hand reference by one frame")
    edit.add_argument("--motion", choices=ADAPTIVE_MOTIONS, help="Edit the horizontal hand or fingertip path")
    args = parser.parse_args()
    try:
        decision = write_candidate(
            args.source, args.output, args.task, args.hand,
            args.start_frame, args.stop_frame, args.offset, motion=args.motion,
        )
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print(json.dumps(decision, indent=2))


if __name__ == "__main__":
    main()
