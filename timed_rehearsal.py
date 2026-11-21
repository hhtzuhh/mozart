"""Compare independent, time-limited continuations of the most-errors rehearsal."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time

import modal

from rehearse import LAB, app, api_key, compare, digest, own_output, practice_trial, save_json
from trajectory_patch import HANDS, trajectory_name


def read(path: Path) -> dict:
    return json.loads(path.read_text())


def checkpoint(previous: Path, initial_trajectories: Path | None = None) -> dict:
    protocol = read(previous / "protocol.json")
    saved = read(previous / "most_errors/results.json")
    if saved not in read(previous / "summary.json")["results"]:
        raise ValueError("The checkpoint must be a completed most-errors experiment")
    original = (previous / protocol["baseline"]).resolve()
    parent = (previous / saved["best_performance"]).resolve()
    source = parent.parent / "trajectories"
    last = saved["trials"][-1]
    if saved["best_round"] == 0:
        source = initial_trajectories or LAB / "baseline/trajectories"
        expected = protocol["initial_trajectories"]
    else:
        edits = read(source / "modification.json")["trajectories"]
        expected = {hand: edits[hand]["output_sha256"] for hand in HANDS}
    if any(digest(source / trajectory_name(protocol["task"], h)) != expected[h] for h in HANDS):
        raise ValueError("Starting hand trajectories differ from the retained checkpoint")
    measured = compare(original / "trace.npz", parent / "trace.npz", 0, protocol["frames"])
    if measured["candidate"]["f1"] != saved["best_f1"]:
        raise ValueError("The retained performance differs from its recorded score")
    feedback = None if last["adopted"] else previous / "most_errors" / f"round-{last['round']}" / "performance"
    if feedback:
        compare(parent / "trace.npz", feedback / "trace.npz", last["start_frame"], last["stop_frame"])
    return {"task": protocol["task"], "seed": protocol["seed"], "state": saved,
            "parent": parent, "source": source, "feedback": feedback, "original": original}


def cutoff_stage(directory: Path) -> str:
    if (directory / "performance").exists():
        return "scoring"
    if (directory / "modal-call.json").exists():
        return "gpu"
    if (directory / "planner.json").exists():
        return "trajectory"
    if any(directory.glob("diagnosis-*.json")):
        return "planning"
    return "diagnosis"


def run_budget(minutes: int, root: Path, start: float, clock_start: float, initial: dict, key: str) -> dict:
    arm = root / f"{minutes}min"
    arm.mkdir(exist_ok=True)
    history = list(initial["state"]["trials"])
    source, parent, feedback = initial["source"], initial["parent"], initial["feedback"]
    best_round = initial["state"]["best_round"]
    deadline = clock_start + minutes * 60
    trials, failures = [], []
    state = {"minutes": minutes, "start_epoch": start, "deadline_epoch": start + minutes * 60,
             "worker_started_seconds": max(0.0, time.monotonic() - clock_start),
             "checkpoint_round": history[-1]["round"], "trials": trials, "failures": failures}
    state_path = arm / "results.json"
    if state_path.exists():
        existing = read(state_path)
        if existing["status"] == "finished":
            return existing
        state["worker_started_seconds"] = existing["worker_started_seconds"]
    best_f1 = initial["state"]["best_f1"]
    state.update(status="running", best_round=best_round, best_f1=best_f1,
                 best_performance=os.path.relpath(parent, root))
    print(f"{minutes} min: start F1 {best_f1:.6f}, deadline {state['deadline_epoch']:.3f}", flush=True)
    index = 0
    while True:
        index += 1
        directory = arm / f"attempt-{index:02d}"
        scored = directory / "scored.json"
        failed = directory / "failure.json"
        if failed.is_file():
            failures.append(read(failed))
            continue
        if scored.is_file():
            entry = read(scored)
            candidate, performance = directory / "trajectories", directory / "performance"
        else:
            save_json(state_path, state)
            if time.monotonic() >= deadline:
                break
            directory.mkdir(exist_ok=True)
            started = directory / "start.json"
            if not started.exists():
                save_json(started, {"elapsed_seconds": time.monotonic() - clock_start})
            try:
                record, candidate, performance = practice_trial(
                    "most_errors", initial["task"], key, directory, source, parent, feedback,
                    history, best_round, deadline=deadline,
                )
                finished = time.monotonic()
                if finished >= deadline:
                    break
            except TimeoutError:
                break
            except (RuntimeError, ValueError) as error:
                if time.monotonic() >= deadline:
                    break
                transient = any(t in str(error).lower() for t in (
                    "429", "500", "502", "503", "504", "timed out", "request failed", "no new executable ideas"))
                if not transient:
                    raise
                failure = {"attempt": index, "elapsed_seconds": time.monotonic() - clock_start,
                           "stage": cutoff_stage(directory), "error": str(error)}
                save_json(failed, failure)
                failures.append(failure)
                time.sleep(max(0.0, min(10.0, deadline - time.monotonic())))
                continue
            entry = {"attempt": index, "started_seconds": read(started)["elapsed_seconds"],
                     "scored_seconds": finished - clock_start, "record": record}
            save_json(scored, entry)
        record = entry["record"]
        if (entry["scored_seconds"] >= minutes * 60 or record["parent_id"] != digest(parent / "trace.npz")
                or record["round"] != len(history) + 1 or record["parent_round"] != best_round):
            raise ValueError(f"Saved time or lineage differs in {directory}")
        trials.append(entry)
        history.append(record)
        if record["adopted"]:
            best_round, best_f1 = record["round"], record["trial_f1"]
            source, parent, feedback = candidate, performance, None
        else:
            feedback = performance
        state.update(best_round=best_round, best_f1=best_f1, best_performance=os.path.relpath(parent, root))
        save_json(state_path, state)
        print(f"{minutes} min, {entry['scored_seconds']:.1f}s: trial {record['trial_f1']:.6f}, best {best_f1:.6f}", flush=True)
    stage = cutoff_stage(directory) if directory.exists() else "between_trials"
    if stage == "gpu" and not (directory / "cancelled.json").exists():
        call_id = read(directory / "modal-call.json")["call_id"]
        modal.FunctionCall.from_id(call_id).cancel(terminate_containers=True)
        save_json(directory / "cancelled.json", {"call_id": call_id})
    state.update(status="finished", stopped_seconds=time.monotonic() - clock_start, cutoff_stage=stage,
                 unfinished_attempt=index if directory.exists() else None)
    save_json(state_path, state)
    print(f"{minutes} min: finished with {len(trials)} scored trials, best {best_f1:.6f}", flush=True)
    return state


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--previous", type=Path, default=LAB / "whole-clip-rehearsal-01")
    parser.add_argument("--initial-trajectories", type=Path,
                        help="Original hand files if the previous run retained its initial performance")
    parser.add_argument("--minutes", type=int, nargs="+", default=[15, 20, 25, 30])
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if (len(set(args.minutes)) != len(args.minutes) or min(args.minutes) < 1 or len(args.minutes) > 4):
        parser.error("Provide up to four distinct positive time budgets")
    root, previous = args.output.resolve(), args.previous.resolve()
    initial = checkpoint(previous, args.initial_trajectories)
    settings = {"strategy": "most_errors", "minutes": args.minutes, "task": initial["task"],
                "seed": initial["seed"], "checkpoint": os.path.relpath(previous, root),
                "history_sha256": digest(previous / "most_errors/results.json"),
                "parent_trace_sha256": digest(initial["parent"] / "trace.npz"),
                "starting_f1": initial["state"]["best_f1"],
                "starting_trajectories": {h: digest(initial["source"] / trajectory_name(initial["task"], h)) for h in HANDS},
                "clock": "Common wall-clock start. Gemini, queue, GPU, download and scoring count.",
                "selection": "Independent branches. Only full-clip F1 gains scored before the branch deadline are retained."}
    with own_output(root):
        protocol_path = root / "protocol.json"
        if protocol_path.is_file():
            old = read(protocol_path)
            if old["settings"] != settings:
                parser.error("Output directory belongs to a different time-budget experiment")
            if (root / "summary.json").is_file():
                print((root / "summary.json").read_text())
                return
        key = api_key(args.env_file)
        with modal.enable_output(), app.run(), ThreadPoolExecutor(max_workers=len(args.minutes)) as pool:
            if protocol_path.exists():
                start = read(protocol_path)["start_epoch"]
            else:
                start = time.time()
                save_json(protocol_path, {"start_epoch": start, "started_utc": datetime.fromtimestamp(start, timezone.utc).isoformat(),
                                         "settings": settings})
            # A restart keeps the persisted wall-clock start. Live deadlines use the monotonic clock.
            clock_start = time.monotonic() - (time.time() - start)
            jobs = [pool.submit(run_budget, m, root, start, clock_start, initial, key) for m in args.minutes]
            results = [job.result() for job in jobs]
        save_json(root / "summary.json", {"start_epoch": start, "starting_f1": settings["starting_f1"], "results": results})


if __name__ == "__main__":
    main()
