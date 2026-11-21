"""Package the six-round strategy branches for the file:// rehearsal studio."""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

from export_rehearsal import HERE, load_trace, note_segments, pitches, read, window_scores
from paired_media import render_pair, write_midi


def package(runs, recording):
    original = (recording / "session.js").read_text().removeprefix("window.REHEARSAL = ").strip().removesuffix(";")
    demo = json.loads(original)
    step = demo["step"]
    output = recording / "strategies"
    output.mkdir(exist_ok=True)
    sessions = {}
    for run in runs:
        protocol = read(run / "protocol.json")
        minutes = next(m for m, count in protocol["rounds_by_estimated_minutes"].items() if count == 6)
        target, baseline, _, seed = load_trace(run / protocol["baseline"] / "trace.npz")
        if (protocol["task"] != demo["task"] or note_segments(target) != demo["notes"]
                or note_segments(baseline) != demo["playedNotes"]["0"]):
            raise ValueError("The recording and strategy run must share their task and baseline keys")
        for strategy in protocol["strategies"]:
            folder = run / strategy / f"{minutes}min"
            results = read(folder / "results.json") if (folder / "results.json").exists() else {"trials": [], "best_round": 0, "best_f1": demo["baseline"]}
            takes = {0: baseline}
            media = {0: "recording/take-0"}
            rounds = []
            for trial in results["trials"]:
                n, parent = trial["round"], takes[trial["parent_round"]]
                directory = folder / f"round-{n}"
                proposal = read(directory / "proposal.json")
                keys, played, _, recorded_seed = load_trace(directory / "performance/trace.npz")
                if recorded_seed != seed or not np.array_equal(keys, target):
                    raise ValueError(f"Incompatible replay in {directory}")
                takes[n] = played
                ideas = []
                for idea in proposal["ideas"]:
                    frame = idea["evidence_frame"]
                    ideas.append(dict(idea, target=pitches(target[frame]), before=pitches(parent[frame]), after=pitches(played[frame])))
                choices = (proposal["bundles"][proposal["choice"]-1] if "bundles" in proposal else [proposal["choice"]])
                rounds.append(dict(trial, choice=choices[0]-1, choices=[i-1 for i in choices], ideas=ideas,
                                   rationale=proposal["rationale"], windows=window_scores(target, parent, played, step)))
                video = directory / "performance/performance.mp4"
                key = hashlib.sha256(video.read_bytes()).hexdigest()[:24]
                stem = output / f"take-{key}"
                if not all((output / (stem.name + suffix)).exists() for suffix in (".mp4", "-score.mp4", ".mid")):
                    render_pair(recording / "score.mp4", video, output, key, len(target), step)
                    write_midi(stem.with_suffix(".mid"), note_segments(played), len(target), step)
                media[n] = "recording/strategies/" + stem.name
            best = results["best_round"]
            sessions[strategy] = dict(rounds=rounds, bestRound=best, best=results["best_f1"], media=media,
                                      playedNotes={n: note_segments(keys) for n, keys in takes.items()},
                                      windows=window_scores(target, baseline, takes[best], step))
            print(strategy, len(rounds), flush=True)
    (output / "sessions.js").write_text("window.STRATEGY_SESSIONS = " + json.dumps(sessions, separators=(",", ":")) + ";\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runs", type=Path, nargs="+")
    args = parser.parse_args()
    package(args.runs, HERE / "recording")
