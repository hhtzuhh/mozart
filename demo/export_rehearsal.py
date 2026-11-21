"""Package one completed engine rehearsal for the offline browser demo."""

import argparse
import json
from pathlib import Path
import sys
import wave

import numpy as np

from paired_media import render_pair, render_reference, write_midi

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
from compare_rollouts import load_trace, score


def read(path):
    return json.loads(path.read_text())


def window_scores(target, before, after, step):
    windows = []
    size = max(1, round(2 / step))
    for start in range(0, len(target), size):
        stop = min(start + size, len(target))
        windows.append({
            "start": start, "stop": stop,
            "before": score(target, before, start, stop)["window_f1"],
            "after": score(target, after, start, stop)["window_f1"],
        })
    return windows


def note_segments(target):
    spans = []
    for key, frames in enumerate(target.T):
        transitions = np.flatnonzero(np.diff(frames.astype(int), prepend=0, append=0))
        spans.extend([int(a), int(b), key + 21] for a, b in transitions.reshape(-1, 2))
    return spans


def pitches(keys):
    return (np.flatnonzero(keys) + 21).tolist()


def render_score(path, spans, frames, step):
    """Render the evaluation target at its original rehearsal tempo."""
    rate = 32000
    samples = np.zeros(round(frames * step * rate))
    for start, stop, midi in spans:
        onset = round(start * step * rate)
        held = (stop - start) * step
        length = min(len(samples) - onset, round((held + 0.12) * rate))
        t = np.arange(length) / rate
        freq = 440 * 2 ** ((midi - 69) / 12)
        tone = np.zeros(length)
        for harmonic, gain in enumerate((1, .46, .24, .12, .055, .022), 1):
            if harmonic * freq < rate / 2:
                tone += gain * np.sin(2 * np.pi * harmonic * freq * t) * np.exp(-t * harmonic * 1.3)
        envelope = np.minimum(t / .006, 1) * np.exp(-np.maximum(t - held, 0) / .022)
        samples[onset:onset + length] += tone * envelope
    samples[-min(len(samples), 160):] *= np.linspace(1, 0, min(len(samples), 160))
    pcm = (samples * (26000 / max(1, np.max(np.abs(samples))))).astype("<i2")
    with wave.open(str(path), "wb") as audio:
        audio.setparams((1, 2, rate, len(pcm), "NONE", "not compressed"))
        audio.writeframes(pcm.tobytes())


def export(run, output, title):
    protocol = read(run / "protocol.json")
    folder = run / "gemini_windows"
    results = read(folder / "results.json")
    baseline = run / protocol["baseline"]
    step = protocol["step_seconds"]
    target, original, _, seed = load_trace(baseline / "trace.npz")
    output.mkdir(parents=True, exist_ok=True)
    sources = {0: baseline / "performance.mp4"}
    takes = {0: original}
    rounds = []
    for trial in results["trials"]:
        number, parent = trial["round"], trial["parent_round"]
        path = folder / f"round-{number}"
        proposal = read(path / "proposal.json")
        current_target, played, _, current_seed = load_trace(path / "performance/trace.npz")
        if current_seed != seed or not np.array_equal(target, current_target):
            raise ValueError(f"Incompatible target or seed in round {number}")
        takes[number] = played
        sources[number] = path / "performance/performance.mp4"
        ideas = []
        for idea in proposal["ideas"]:
            frame = idea["evidence_frame"]
            ideas.append(dict(idea, target=pitches(target[frame]), before=pitches(takes[parent][frame]),
                              after=pitches(played[frame])))
        rounds.append(dict(trial, ideas=ideas, choice=proposal["choice"] - 1,
                           rationale=proposal["rationale"],
                           windows=window_scores(target, takes[parent], played, step)))
    best = results["best_round"]
    comparison = []
    for strategy in protocol["strategies"]:
        path = run / strategy / "results.json"
        if path.is_file():
            arm = read(path)
            comparison.append({"strategy": strategy, "best": arm["best_f1"], "count": len(arm["trials"])})
    spans = note_segments(target)
    render_score(output / "score.wav", spans, len(target), step)
    write_midi(output / "score.mid", spans, len(target), step)
    reference = output / "score.mp4"
    render_reference(reference, target, spans, step, output / "score.wav")
    played_notes = {n: note_segments(keys) for n, keys in takes.items()}
    for n, source in sources.items():
        write_midi(output / f"take-{n}.mid", played_notes[n], len(target), step)
        render_pair(reference, source, output, n, len(target), step)
    data = {
        "title": title or protocol["task"], "task": protocol["task"], "step": step,
        "frames": len(target), "baseline": protocol["baseline_f1"], "best": results["best_f1"],
        "bestRound": best, "rounds": rounds, "notes": spans, "playedNotes": played_notes, "scoreAudio": "recording/score.wav",
        "windows": window_scores(target, original, takes[best], step), "strategies": comparison,
    }
    # A local script permits playback directly from index.html under file://.
    (output / "session.js").write_text("window.REHEARSAL = " + json.dumps(data, separators=(",", ":")) + ";\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--output", type=Path, default=HERE / "recording")
    parser.add_argument("--title")
    args = parser.parse_args()
    export(args.run, args.output, args.title)
