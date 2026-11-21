"""Run four independent rehearsal strategies at five fixed replay budgets."""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
import time

import modal

from rehearse import (DIAGNOSER, HANDS, HAND_MOTIONS, LAB, PLANNER, UPSTREAM_SHA, api_key, app,
                      digest, load_trace, own_output, practice_trial, save_json, score, trajectory_name)
from rehearsal_plan import ADAPTIVE, MATRIX_STRATEGIES
from trajectory_patch import ADAPTIVE_MOTIONS


BUDGETS = {15: 2, 20: 3, 25: 4, 30: 5, 35: 6}


def read(path: Path) -> dict:
    return json.loads(path.read_text())


def trial_with_recovery(strategy, task, key, directory, source, parent, feedback, history, best_round):
    errors = directory / "retries.json"
    retries = read(errors)["events"] if errors.exists() else []
    while True:
        try:
            return practice_trial(strategy, task, key, directory, source, parent, feedback, history, best_round)
        except (RuntimeError, ValueError, modal.exception.Error) as error:
            msg = str(error)
            rejected = ("no new executable ideas" in msg or "Planner selected an unavailable edit" in msg)
            remote_failed = isinstance(error, (modal.exception.FunctionTimeoutError, modal.exception.OutputExpiredError))
            transient = any(t in msg.lower() for t in (
                "http 429", "http 500", "http 502", "http 503", "http 504", "request failed",
                "did not complete the interaction", "did not return a json object", "unavailable",
                "connection reset", "resource exhausted"))
            if not (rejected or transient or remote_failed):
                raise
            retries.append({"epoch": time.time(), "error": msg, "type": type(error).__name__})
            save_json(errors, {"events": retries})
            archive = directory / f"retry-{len(retries):02d}"
            if rejected:
                archive.mkdir()
                for file in [*directory.glob("diagnosis-*.json"), directory / "planner.json"]:
                    if file.exists():
                        file.rename(archive / file.name)
            if remote_failed:
                archive.mkdir(exist_ok=True)
                (directory / "modal-call.json").rename(archive / "modal-call.json")
            print(f"{strategy}/{directory.parent.name}/{directory.name}: retry {len(retries)}: {msg[:140]}", flush=True)
            time.sleep(min(60, 5 * len(retries)))


def run_group(args, strategy: str, minutes: int, key: str, base_f1: float) -> dict:
    root = args.output
    group = root / strategy / f"{minutes}min"
    group.mkdir(parents=True, exist_ok=True)
    start_file = group / "start.json"
    if not start_file.exists():
        save_json(start_file, {"epoch": time.time()})
    start = read(start_file)["epoch"]
    history, timings = [], []
    parent, source, feedback = args.baseline, args.trajectories, None
    best_round, best = 0, base_f1
    count = BUDGETS[minutes]
    state = {"strategy": strategy, "estimated_minutes": minutes, "required_rounds": count,
             "start_epoch": start, "status": "running", "trials": history, "timings": timings,
             "best_round": 0, "best_f1": base_f1, "best_performance": os.path.relpath(parent, root)}
    for i in range(1, count + 1):
        directory = group / f"round-{i}"
        directory.mkdir(exist_ok=True)
        receipt = directory / "scored.json"
        if not receipt.is_file():
            save_json(group / "results.json", state)
            begin = directory / "start.json"
            if not begin.is_file():
                save_json(begin, {"epoch": time.time()})
            record, _, _ = trial_with_recovery(strategy, args.task, key, directory, source, parent,
                                               feedback, history, best_round)
            save_json(receipt, {"start_epoch": read(begin)["epoch"], "scored_epoch": time.time(), "record": record})
        trial = read(receipt)
        record = trial["record"]
        if (record["round"] != i or record["parent_round"] != best_round or record["parent_f1"] != best
                or record["parent_id"] != digest(parent / "trace.npz")
                or record["adopted"] != (record["trial_f1"] > best)):
            raise ValueError(f"Saved rehearsal lineage differs in {directory}")
        history.append(record)
        timings.append({"round": i, "seconds": trial["scored_epoch"] - trial["start_epoch"],
                        "elapsed_seconds": trial["scored_epoch"] - start})
        if record["adopted"]:
            best_round, best = i, record["trial_f1"]
            parent, source, feedback = directory / "performance", directory / "trajectories", None
        else:
            feedback = directory / "performance"
        state.update(best_round=best_round, best_f1=best, best_performance=os.path.relpath(parent, root),
                     elapsed_seconds=timings[-1]["elapsed_seconds"], status="complete" if i == count else "running")
        save_json(group / "results.json", state)
        print(f"{strategy}/{minutes}min: {i}/{count}, trial {record['trial_f1']:.6f}, best {best:.6f}", flush=True)
    return state


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", default="SomewhereOnlyWeKnow_8")
    parser.add_argument("--baseline", type=Path, default=LAB / "modal-gemini-video/baseline")
    parser.add_argument("--trajectories", type=Path, default=LAB / "baseline/trajectories")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--adaptive", action="store_true",
                        help="Run only the six-replay Gemini adaptive strategy in a separate output directory")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    args = parser.parse_args()
    root = args.output = args.output.resolve()
    target, played, _, seed = load_trace(args.baseline / "trace.npz")
    report = read(args.baseline / "result.json")
    base = score(target, played, 0, len(target))["f1"]
    strategies = (ADAPTIVE,) if args.adaptive else MATRIX_STRATEGIES
    budgets = {35: 6} if args.adaptive else BUDGETS
    if (report["task"] != args.task or report["seed"] != seed or not report["has_audio"]
            or abs(report["stock_metrics"]["f1"] - base) > 1e-10):
        raise ValueError("The baseline task, seed, stock F1 and audio must match")
    protocol = {"task": args.task, "seed": seed, "frames": len(target), "baseline_f1": base,
                "baseline": os.path.relpath(args.baseline, root), "trajectories": os.path.relpath(args.trajectories, root),
                "baseline_trace_sha256": digest(args.baseline / "trace.npz"),
                "baseline_video_sha256": digest(args.baseline / "performance.mp4"),
                "hand_sha256": {h: digest(args.trajectories / trajectory_name(args.task, h)) for h in HANDS},
                "strategies": list(strategies), "models": [DIAGNOSER, PLANNER],
                "motions": list(ADAPTIVE_MOTIONS if args.adaptive else HAND_MOTIONS), "pianomime_commit": UPSTREAM_SHA,
                "rounds_by_estimated_minutes": {str(m): n for m, n in budgets.items()},
                "design": "Twenty independent branches. Every group starts at the unedited baseline with empty history.",
                "stopping": "Finish every assigned physical replay and score it, regardless of elapsed time.",
                "retention": "Strictly higher full-clip mean frame F1. Three-edit bundles are retained or rejected together.",
                "cooldown": "After two consecutive rejections in the same two-second bin, skip it for one replay."}
    if args.adaptive:
        protocol.update(design="One adaptive branch from the original baseline. Gemini selects a tactic each round.",
                        retention="Keep the parent with the strictly higher full-clip mean frame F1.")
        del protocol["cooldown"]
    with own_output(root):
        stamp = root / "protocol.json"
        if stamp.exists() and read(stamp) != protocol:
            raise ValueError("The output directory belongs to another experiment")
        if (root / "summary.json").exists() and stamp.exists():
            summary = read(root / "summary.json")
            print(f"{len(summary['groups'])} groups, {summary['scored_replays']} completed replays")
            return
        save_json(stamp, protocol)
        key = api_key(args.env_file)
        groups = [(s, m) for m in reversed(budgets) for s in strategies]
        with modal.enable_output(), app.run(detach=True), ThreadPoolExecutor(max_workers=len(groups)) as pool:
            jobs = [pool.submit(run_group, args, s, m, key, base) for s, m in groups]
            results = [job.result() for job in as_completed(jobs)]
        results.sort(key=lambda r: (strategies.index(r["strategy"]), r["estimated_minutes"]))
        total = sum(len(r["trials"]) for r in results)
        if total != sum(budgets.values()) * len(strategies):
            raise RuntimeError("Missing scored replays")
        save_json(root / "summary.json", {"baseline_f1": base, "scored_replays": total, "groups": results})
        print(f"Completed {len(results)} groups and {total} scored replays", flush=True)


if __name__ == "__main__":
    main()
