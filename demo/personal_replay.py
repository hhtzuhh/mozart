"""Publish one live run's scored checkpoints to the local saved-rehearsal player."""
import json
import traceback
from pathlib import Path
from tempfile import NamedTemporaryFile

from export_strategy_studio import HERE, checked_baseline, package_branch, read, reference_data


def publish(run):
    config = read(run / "request.json")
    strategy, count = config["strategy"], config["rounds"]
    folder = run / strategy / f"{5 * (count + 1)}min"
    status = folder / "results.json"
    if not status.exists():
        return False
    results = read(status)
    finished = len(results["trials"]) == count
    output = HERE / ".local/replays" / run.name
    manifest = output / "session.json"
    if manifest.is_file() and len(read(manifest)["rounds"]) == len(results["trials"]):
        return finished
    output.mkdir(parents=True, exist_ok=True)
    recording = HERE / "recording"
    reference = reference_data(recording)
    protocol = read(run / "protocol.json")
    target, baseline, seed = checked_baseline(run, protocol, reference)
    branch = package_branch(folder, results, recording, output, target, baseline, seed, reference["step"])
    branch.update(runId=run.name, strategy=strategy, budget=count, created=config["created"])
    with NamedTemporaryFile(mode="w", dir=output, delete=False) as fp:
        json.dump(branch, fp, separators=(",", ":"))
    Path(fp.name).replace(manifest)
    return finished


def follow(run, stop):
    while not stop.is_set():
        try:
            if publish(run) or (run / "failure.json").exists():
                return
        except Exception:
            traceback.print_exc()
        stop.wait(3)
    try:
        publish(run)
    except Exception:
        traceback.print_exc()
