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


def call_receipt(directory):
    try:
        with (directory / "modal-call.json").open() as receipt:
            return {"id": json.load(receipt)["call_id"], "submitted": os.fstat(receipt.fileno()).st_mtime}
    except FileNotFoundError:
        return None


def movement(edit):
    return {"hand": edit["hand"], "motion": edit["motion"],
            "startFrame": edit["start_frame"], "stopFrame": edit["stop_frame"]}


def branch_state(group, required, baseline):
    results = read(group / "results.json", {})
    trials = results.get("trials", [])
    count = len(trials)
    current = group / f"round-{count + 1}"
    receipt = call_receipt(current)
    waiting_call = None
    retries = read(current / "retries.json", {}).get("events", [])
    if count == required:
        phase = "Complete"
    elif (current / "scored.json").is_file():
        phase = "Score saved · updating branch"
    elif (current / "comparison.json").is_file():
        phase = "Comparison saved"
    elif (current / "performance/result.json").is_file():
        phase = "Rollout received · verifying score"
    elif receipt:
        phase = "Submitted to Modal · awaiting result"
        waiting_call = receipt
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
        directory = group / f"round-{trial['round']}"
        video = directory / "performance/performance.mp4"
        scored_call = call_receipt(directory)
        edits = trial["edits"] if "edits" in trial else [trial]
        history.append({
            "number": trial["round"], "f1": trial["trial_f1"], "gain": trial["trial_f1"] - trial["parent_f1"],
            "retained": trial["adopted"], "video": os.path.relpath(video, DEMO) if video.is_file() else None,
            "callId": scored_call["id"] if scored_call else None,
            "edits": [movement(e) for e in edits], "parentF1": trial["parent_f1"],
        })
        if "tactic" in trial:
            proposal = read(directory / "proposal.json", {})
            history[-1].update(tactic=trial["tactic"], rationale=trial["rationale"],
                               noteCheck=trial["note_check"], motion=trial["motion"],
                               choice=proposal.get("choice"), candidates=len(proposal.get("ideas", [])))
    proposal = read(current / "proposal.json", {}) if count < required else {}
    selected = proposal.get("selected", {})
    current_edits = proposal.get("selected_edits", [selected] if selected else [])
    return {
        "scored": count, "phase": phase, "retries": len(retries), "best": results.get("best_f1", baseline),
        "bestRound": results.get("best_round", 0), "history": history, "waitingCall": waiting_call,
        "currentTactic": selected.get("tactic"), "currentEdits": [movement(e) for e in current_edits],
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
    parser.add_argument("--adaptive-run", type=Path, help="Include the adaptive branch on the six-round comparison page")
    args = parser.parse_args()
    root = args.run.resolve()
    folder = DEMO / ".local"
    folder.mkdir(exist_ok=True)
    feed = folder / ("adaptive-progress.js" if args.adaptive_run else "matrix-progress-v2.js")
    temporary = folder / f"matrix-{os.getpid()}.tmp"
    try:
        while True:
            state = snapshot(root)
            if args.adaptive_run and not state.get("waiting"):
                adaptive_root = args.adaptive_run.resolve()
                original = read(root / "protocol.json")
                adaptive = read(adaptive_root / "protocol.json")
                if adaptive and (adaptive["strategies"] != ["gemini_adaptive"] or any(
                    adaptive[k] != original[k] for k in ("task", "seed", "baseline_trace_sha256",
                                                          "baseline_video_sha256", "hand_sha256"))):
                    raise ValueError("Adaptive comparison requires the same baseline and a six-round adaptive run")
                groups = {name: {6: arm[6]} for name, arm in state["groups"].items()}
                branch = branch_state(adaptive_root / "gemini_adaptive/35min", 6, state["baseline"])
                if not adaptive:
                    branch["phase"] = "Awaiting adaptive run"
                groups["gemini_adaptive"] = {6: branch}
                state.update(groups=groups, total=6 * len(groups),
                             scored=sum(arm[6]["scored"] for arm in groups.values()),
                             finished=all(arm[6]["scored"] == 6 for arm in groups.values()))
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
