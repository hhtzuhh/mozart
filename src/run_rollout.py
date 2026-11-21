"""Run one PianoMime rollout with isolated inputs, video, trace, and scores."""

import argparse
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

from compare_rollouts import compare, load_trace, score
from trajectory_patch import HANDS, trajectory_name


def stage_workspace(runtime: Path, trajectories: Path, task: str, output: Path) -> Path:
    upstream = runtime / "pianomime"
    source_multi_task = upstream / "multi_task"
    inputs = (
        runtime / "dataset",
        runtime / "checkpoint_ae.ckpt",
        runtime / "checkpoint_low_level.ckpt",
    )
    hand_files = [trajectories / trajectory_name(task, hand) for hand in HANDS]
    required_files = [
        source_multi_task / "eval_low_level.py",
        runtime / "checkpoint_ae.ckpt",
        runtime / "checkpoint_low_level.ckpt",
        *hand_files,
    ]
    for file in required_files:
        if not file.is_file():
            raise FileNotFoundError(f"Missing rollout input: {file}")
    for directory in (runtime / "dataset", upstream / "dataset_ll.zarr"):
        if not directory.is_dir():
            raise FileNotFoundError(f"Missing rollout input: {directory}")

    output.mkdir(parents=True, exist_ok=False)
    workspace = output / "workspace"
    staged_repo = workspace / "pianomime"
    staged_multi_task = staged_repo / "multi_task"
    staged_trajectories = staged_multi_task / "trajectories"
    staged_trajectories.mkdir(parents=True)

    for item in inputs:
        (workspace / item.name).symlink_to(item.resolve(), target_is_directory=item.is_dir())
    for source, destination, excluded in (
        (upstream, staged_repo, {".git", "multi_task", "__pycache__"}),
        (source_multi_task, staged_multi_task, {"trajectories", "__pycache__"}),
    ):
        for item in source.iterdir():
            if item.name not in excluded:
                (destination / item.name).symlink_to(item.resolve(), target_is_directory=item.is_dir())
    for file in hand_files:
        shutil.copy2(file, staged_trajectories / file.name)
    return workspace


def recorded_scores(log: Path) -> dict[str, float]:
    text = log.read_text(errors="replace")
    metrics = {}
    for name in ("Precision", "Recall", "F1"):
        values = re.findall(rf"^{name}:\s+(\S+)", text, flags=re.M)
        if not values:
            raise ValueError(f"PianoMime did not report {name}; see {log}")
        value = float(values[-1])
        if not 0 <= value <= 1:
            raise ValueError(f"Invalid PianoMime {name}: {value}")
        metrics[name.lower()] = value
    return metrics


def run(args: argparse.Namespace) -> dict:
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        raise FileNotFoundError("FFmpeg is required. Install ffprobe on the GPU host.")
    if not 0 <= args.seed < 2**32:
        raise ValueError("Seed must be an unsigned 32-bit integer")
    baseline = load_trace(args.baseline_trace) if args.baseline_trace else None
    if baseline is not None and baseline[3] != args.seed:
        raise ValueError("The baseline trace must use the requested seed")
    if args.window is not None:
        start, stop = args.window
        if not 0 <= start < stop or (baseline is not None and stop > len(baseline[0])):
            raise ValueError("The analysis window must be within the rollout")

    output = args.output.resolve()
    workspace = stage_workspace(args.runtime, args.trajectories, args.task, output)
    trace_path = output / "trace.npz"
    log_path = output / "rollout.log"
    env = dict(os.environ)
    env.update(
        MUJOCO_GL=args.renderer,
        XLA_PYTHON_CLIENT_PREALLOCATE="false",
        PYTHONHASHSEED=str(args.seed),
        PYTHONDONTWRITEBYTECODE="1",
    )
    for key, default in {
        "CUDA_VISIBLE_DEVICES": "0",
        "MUJOCO_EGL_DEVICE_ID": "0",
        "OMP_NUM_THREADS": "2",
        "MKL_NUM_THREADS": "2",
    }.items():
        env.setdefault(key, default)

    print(f"Running {args.task}. Follow progress in {log_path}", file=sys.stderr, flush=True)
    started = time.monotonic()
    with log_path.open("wb") as log:
        result = subprocess.run(
            [sys.executable, "-u", str(Path(__file__).resolve().with_name("record_rollout.py")),
             "--seed", str(args.seed), "--trace", str(trace_path), "--", args.task],
            cwd=workspace, env=env, stdout=log, stderr=subprocess.STDOUT,
        )
    elapsed = time.monotonic() - started
    if result.returncode:
        raise RuntimeError(f"PianoMime exited with code {result.returncode}; see {log_path}")

    stock = recorded_scores(log_path)
    target, played, _, _ = load_trace(trace_path)
    start, stop = args.window or (0, len(target))
    if not 0 <= start < stop <= len(target):
        raise ValueError("The analysis window must be within the completed rollout")
    measured = score(target, played, start, stop)
    if not math.isclose(measured["f1"], stock["f1"], rel_tol=0, abs_tol=1e-10):
        raise RuntimeError(f"Recorded keys disagree with PianoMime's F1; see {log_path}")

    videos = list(workspace.glob("*.mp4"))
    if len(videos) != 1:
        raise RuntimeError(f"Expected one PianoMime video; found {len(videos)} in {workspace}")
    media = json.loads(subprocess.check_output(
        [ffprobe, "-v", "error", "-show_entries", "stream=codec_type:format=duration",
         "-of", "json", str(videos[0])], text=True,
    ))
    stream_types = {stream["codec_type"] for stream in media["streams"]}
    if "video" not in stream_types:
        raise RuntimeError("PianoMime's output does not contain a video stream")
    video_path = output / "performance.mp4"
    videos[0].rename(video_path)

    report = {
        "task": args.task,
        "seed": args.seed,
        "renderer": args.renderer,
        "frames": len(target),
        "wall_time_seconds": elapsed,
        "duration_seconds": float(media["format"]["duration"]),
        "has_audio": "audio" in stream_types,
        "video": video_path.name,
        "trace": trace_path.name,
        "stock_metrics": stock,
        "key_metrics": measured,
    }
    if args.baseline_trace:
        report["comparison"] = compare(args.baseline_trace, trace_path, start, stop)
    (output / "result.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task")
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--trajectories", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--baseline-trace", type=Path)
    parser.add_argument("--window", type=int, nargs=2, metavar=("START", "STOP_EXCLUSIVE"))
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--renderer", choices=("egl", "osmesa"), default="egl")
    args = parser.parse_args()
    try:
        report = run(args)
    except (OSError, KeyError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Rollout failed: {error}\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
