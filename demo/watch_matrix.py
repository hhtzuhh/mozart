"""Mirror engine checkpoints into a local feed for the browser progress view."""
import argparse
import json
import os
import time
from pathlib import Path

DEMO = Path(__file__).resolve().parent
DEFAULT_RUN = DEMO.parents[1] / "experiments/pianomime/strategy-matrix-01"


def read(path, default=None):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return default


def branch_state(group, required, baseline):
    results = read(group / "results.json", {})
    trials = results.get("trials", [])
    count = len(trials)
    current = group / f"round-{count + 1}"
    retries = read(current / "retries.json", {}).get("events", [])
    if count == required:
        phase = "Complete"
    elif (current / "scored.json").is_file():
        phase = "Score saved · updating branch"
    elif (current / "comparison.json").is_file():
        phase = "Comparison saved"
    elif (current / "performance/result.json").is_file():
        phase = "Rollout received · verifying score"
    elif (current / "modal-call.json").is_file():
        phase = "Submitted to Modal · awaiting result"
    elif (current / "proposal.json").is_file():
        phase = "Edit selected · preparing rollout"
    elif (current / "planner.json").is_file() or any(current.glob("diagnosis-*.json")):
        phase = "Diagnosis received · selecting edits"
    elif (current / "start.json").is_file():
        phase = "Round started · awaiting diagnosis"
    else:
        phase = "Awaiting round start"
    history = []
    for trial in trials:
        video = group / f"round-{trial['round']}" / "performance/performance.mp4"
        history.append({
            "number": trial["round"], "f1": trial["trial_f1"], "gain": trial["trial_f1"] - trial["parent_f1"],
            "retained": trial["adopted"], "video": os.path.relpath(video, DEMO) if video.is_file() else None,
        })
    return {
        "scored": count, "phase": phase, "retries": len(retries), "best": results.get("best_f1", baseline),
        "bestRound": results.get("best_round", 0), "history": history,
    }


def snapshot(root):
    protocol = read(root / "protocol.json")
    if not protocol:
        return {"run": root.name, "waiting": True}
    budgets = protocol["rounds_by_estimated_minutes"]
    baseline = protocol["baseline_f1"]
    groups = {strategy: {count: branch_state(root / strategy / f"{minutes}min", count, baseline)
                         for minutes, count in budgets.items()} for strategy in protocol["strategies"]}
    return {
        "run": root.name, "song": protocol["task"], "baseline": baseline, "groups": groups,
        "scored": sum(g["scored"] for arm in groups.values() for g in arm.values()),
        "total": len(groups) * sum(budgets.values()), "finished": (root / "summary.json").is_file(),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path, nargs="?", default=DEFAULT_RUN)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    root = args.run.resolve()
    folder = DEMO / ".local"
    folder.mkdir(exist_ok=True)
    feed = folder / "matrix-progress.js"
    temporary = folder / f"matrix-{os.getpid()}.tmp"
    try:
        while True:
            state = snapshot(root)
            state["observed"] = time.time()
            temporary.write_text("window.matrixSnapshot = " + json.dumps(state, separators=(",", ":")) + ";\n")
            temporary.replace(feed)
            if args.once or state.get("finished"):
                break
            time.sleep(3)
    finally:
        temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
