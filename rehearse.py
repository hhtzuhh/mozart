"""Run equal-budget, full-clip PianoMime rehearsals with two window-selection rules."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import errno
import hashlib
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

import modal

from modal_rollout import LAB, OUTPUTS, SUBMISSION, UPSTREAM_SHA, app, rollout

sys.path.insert(0, str(SUBMISSION / "src"))
from compare_rollouts import compare, load_trace, score
from gemini_proposal import api_key
from rehearsal_plan import DIAGNOSER, PLANNER, STRATEGIES, edit_id, make_plan, save_json, seconds_left
from trajectory_patch import HANDS, HAND_MOTIONS, trajectory_name, write_edits


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def simulate(directory: Path, trajectories: Path, task: str, start: int, stop: int, seed: int,
             *, deadline: float | None = None) -> Path:
    seconds_left(deadline, float("inf"))
    performance = directory / "performance"
    if performance.is_dir():
        return performance
    receipt = directory / "modal-call.json"
    if receipt.is_file():
        call = modal.FunctionCall.from_id(json.loads(receipt.read_text())["call_id"])
    else:
        hands = {h: (trajectories / trajectory_name(task, h)).read_bytes() for h in HANDS}
        call = rollout.spawn(task, hands, start, stop, seed)
        save_json(receipt, {"call_id": call.object_id})
    print(f"{directory.parent.name}/{directory.name}: GPU call {call.object_id}", flush=True)
    if deadline is None:
        files = call.get()
    else:
        try:
            files = call.get(timeout=seconds_left(deadline, float("inf")))
        except (TimeoutError, modal.exception.TimeoutError):
            call.cancel(terminate_containers=True)
            save_json(directory / "cancelled.json", {"call_id": call.object_id})
            raise TimeoutError("Time budget expired while waiting for the GPU") from None
    with TemporaryDirectory(dir=directory) as temp:
        staged = Path(temp) / "performance"
        staged.mkdir()
        for name in OUTPUTS:
            (staged / name).write_bytes(files[name])
        staged.rename(performance)
    return performance


def practice_trial(strategy: str, task: str, key: str, directory: Path, source: Path,
                   parent: Path, feedback: Path | None, history: list, best_round: int,
                   *, deadline: float | None = None) -> tuple[dict, Path, Path]:
    number = len(history) + 1
    plan = make_plan(key, strategy, task, source, parent, directory, history, feedback, deadline=deadline)
    triple = strategy == "gemini_three"
    edits = plan["selected_edits"] if triple else [plan["selected"]]
    if len(edits) != (3 if triple else 1):
        raise ValueError(f"Unexpected number of edits in {directory}")
    candidate = directory / "trajectories"
    if not candidate.is_dir():
        write_edits(source, candidate, task, edits)
    modification = json.loads((candidate / "modification.json").read_text())
    saved = modification["edits"] if triple else [modification]
    if [edit_id(e) for e in edits] != [(e["start_frame"], e["stop_frame_exclusive"], e["hand"], e["motion"]) for e in saved]:
        raise ValueError(f"Saved edit differs from the proposal in {directory}")
    for side, hashes in modification["trajectories"].items():
        name = trajectory_name(task, side)
        if digest(source / name) != hashes["input_sha256"] or digest(candidate / name) != hashes["output_sha256"]:
            raise ValueError(f"Saved hand trajectory differs in {directory}")
    print(f"{strategy}/{directory.parent.name} trial {number}: {[edit_id(e) for e in edits]}", flush=True)
    a, b = (0, plan["frames"]) if triple else (edits[0]["start_frame"], edits[0]["stop_frame"])
    performance = simulate(directory, candidate, task, a, b, plan["seed"], deadline=deadline)
    remote = json.loads((performance / "result.json").read_text())
    result = compare(parent / "trace.npz", performance / "trace.npz", a, b)
    if (remote["task"] != task or remote["seed"] != plan["seed"] or not remote["has_audio"]
            or abs(remote["stock_metrics"]["f1"] - result["candidate"]["f1"]) > 1e-10):
        raise RuntimeError(f"Stock score, task, or audio verification failed for {directory}")
    seconds_left(deadline, float("inf"))
    save_json(directory / "comparison.json", result)
    movement = {} if triple else {"start_frame": a, "stop_frame": b, "hand": edits[0]["hand"],
                                  "motion": edits[0]["motion"]}
    local_scores = {} if triple else {"parent_window_f1": result["baseline"]["window_f1"],
                                      "trial_window_f1": result["candidate"]["window_f1"]}
    # The original field order is part of the saved Gemini history prompt.
    record = {
        "round": number, "parent_round": best_round, "parent_id": plan["parent_id"],
        **movement, "parent_f1": result["baseline"]["f1"], "trial_f1": result["candidate"]["f1"],
        **local_scores, "adopted": result["adopt_candidate"],
    }
    if triple:
        target, before, _, _ = load_trace(parent / "trace.npz")
        _, after, _, _ = load_trace(performance / "trace.npz")
        record["edits"] = []
        for edit in edits:
            start, stop, hand, motion = edit_id(edit)
            record["edits"].append({"start_frame": start, "stop_frame": stop, "hand": hand, "motion": motion,
                                    "parent_window_f1": score(target, before, start, stop)["window_f1"],
                                    "trial_window_f1": score(target, after, start, stop)["window_f1"]})
    if strategy == "switch_on_stall":
        record["rule_start"] = plan["rule_region"]["start_frame"]
    return record, candidate, performance


def rehearse_arm(strategy: str, args: argparse.Namespace, key: str) -> dict:
    source, parent, feedback = args.trajectories, args.baseline, None
    best_round = 0
    history = []
    for number in range(1, args.rounds + 1):
        directory = args.output / strategy / f"round-{number}"
        record, candidate, performance = practice_trial(strategy, args.task, key, directory, source,
                                                        parent, feedback, history, best_round)
        history.append(record)
        print(f"{strategy} trial {number}: full F1 {record['parent_f1']:.6f} -> "
              f"{record['trial_f1']:.6f}; retained={record['adopted']}", flush=True)
        if record["adopted"]:
            best_round, source, parent, feedback = number, candidate, performance, None
        else:
            feedback = performance
        summary = {"strategy": strategy, "trials": history, "best_round": best_round,
                   "best_f1": max(record["parent_f1"], record["trial_f1"]),
                   "best_performance": os.path.relpath(parent, args.output)}
        save_json(args.output / strategy / "results.json", summary)
    return summary


@contextmanager
def own_output(directory: Path):
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".run.lock").open("a+b") as lock:
        try:
            if os.name == "nt":
                import msvcrt
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            if error.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                raise ValueError(f"A rehearsal is already running in {directory}") from error
            raise
        yield


def run_experiment(args: argparse.Namespace) -> None:
    target, played, _, seed = load_trace(args.baseline / "trace.npz")
    report = json.loads((args.baseline / "result.json").read_text())
    original = score(target, played, 0, len(target))["f1"]
    if (report["task"] != args.task or report["seed"] != seed or not report["has_audio"]
            or abs(report["stock_metrics"]["f1"] - original) > 1e-10):
        raise ValueError("The baseline task, seed, audio and stock F1 must match its trace")
    protocol = {
        "task": args.task, "seed": seed, "rounds_per_strategy": args.rounds,
        "strategies": list(STRATEGIES), "motions": list(HAND_MOTIONS),
        "models": [DIAGNOSER, PLANNER], "pianomime_commit": UPSTREAM_SHA,
        "control": "The edit must cover the first peak-error frame of the highest-error two-second bin. Ties go to the earliest bin.",
        "edit_length_frames": [2, 80], "step_seconds": 0.05,
        "retention": "Full-clip mean frame F1 strictly exceeds the current parent.",
        "frames": len(target), "baseline_f1": original,
        "baseline": os.path.relpath(args.baseline, args.output),
        "baseline_trace_sha256": digest(args.baseline / "trace.npz"),
        "baseline_video_sha256": digest(args.baseline / "performance.mp4"),
        "initial_trajectories": {h: digest(args.trajectories / trajectory_name(args.task, h)) for h in HANDS},
    }
    protocol_path = args.output / "protocol.json"
    if protocol_path.is_file() and json.loads(protocol_path.read_text()) != protocol:
        raise ValueError("This output directory belongs to a different experiment")
    summary_path = args.output / "summary.json"
    if summary_path.is_file() and protocol_path.is_file():
        saved = json.loads(summary_path.read_text())
        for arm in saved["results"]:
            print(f"{arm['strategy']}: retained F1 {arm['best_f1']:.6f}, round {arm['best_round']}")
        return
    save_json(protocol_path, protocol)
    key = api_key(args.env_file)
    with modal.enable_output(), app.run(), ThreadPoolExecutor(max_workers=len(STRATEGIES)) as pool:
        futures = [pool.submit(rehearse_arm, strategy, args, key) for strategy in STRATEGIES]
        results = [future.result() for future in futures]
    save_json(summary_path, {"baseline_f1": original, "results": results})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", default="SomewhereOnlyWeKnow_8")
    parser.add_argument("--baseline", type=Path, default=LAB / "modal-gemini-video/baseline")
    parser.add_argument("--trajectories", type=Path, default=LAB / "baseline/trajectories")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.rounds < 1:
        parser.error("Specify a positive trial budget")
    try:
        with own_output(args.output):
            run_experiment(args)
    except (OSError, ValueError) as error:
        parser.exit(1, f"Rehearsal failed: {error}\n")


if __name__ == "__main__":
    main()
