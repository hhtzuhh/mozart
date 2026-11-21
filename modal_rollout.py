"""Run the same PianoMime baseline and candidate on separate Modal T4 workers."""

import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

import modal


SUBMISSION = Path(__file__).resolve().parent
LAB = SUBMISSION.parent / "experiments/pianomime"
RUNTIME = "/opt/piano-runtime"
UPSTREAM_SHA = "c4abefac8d2941f5a08ca656b12f5e047cab7155"
OUTPUTS = ("performance.mp4", "trace.npz", "rollout.log", "result.json")

image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install(
        "build-essential", "ffmpeg", "git", "libasound2-dev", "libegl1", "libfluidsynth3",
        "libgl1", "libosmesa6", "portaudio19-dev",
    )
    .run_commands(
        f"git init {RUNTIME}/pianomime",
        f"git -C {RUNTIME}/pianomime fetch --depth=1 https://github.com/sNiper-Qian/pianomime.git {UPSTREAM_SHA}",
        f"git -C {RUNTIME}/pianomime checkout --detach FETCH_HEAD",
        f"sed 's/^quadprog==0.1.11$/quadprog==0.1.13/' {RUNTIME}/pianomime/requirements.txt > /tmp/pianomime-requirements.txt",
        "python -m pip install --no-cache-dir -r /tmp/pianomime-requirements.txt "
        "numpy==1.26.4 scipy==1.11.4 torch==2.4.1 dm-control==1.0.15 numcodecs==0.11.0 "
        "huggingface-hub==0.20.3 'jax[cpu]==0.4.23' einops ipython gdown",
    )
    .add_local_file(SUBMISSION / "src/fetch_pianomime_assets.py", "/tmp/fetch_pianomime_assets.py", copy=True)
    .run_commands(f"python /tmp/fetch_pianomime_assets.py {RUNTIME}")
    .add_local_dir(SUBMISSION / "src", remote_path="/opt/piano-src", ignore=["**/__pycache__/**"])
)

app = modal.App("pianomime-rollouts", image=image)


@app.function(gpu="T4", cpu=2, memory=8192, timeout=1500,
              max_containers=int(os.environ.get("PIANOMIME_GPU_WORKERS", "9")), retries=2)
def rollout(task: str, hands: dict[str, bytes], start: int, stop: int, seed: int) -> dict[str, bytes]:
    sys.path.insert(0, "/opt/piano-src")
    from trajectory_patch import HANDS, trajectory_name

    with TemporaryDirectory() as temp:
        root = Path(temp)
        trajectories = root / "trajectories"
        trajectories.mkdir()
        for hand in HANDS:
            (trajectories / trajectory_name(task, hand)).write_bytes(hands[hand])

        output = root / "rollout"
        result = subprocess.run(
            [sys.executable, "/opt/piano-src/run_rollout.py", task,
             "--runtime", RUNTIME, "--trajectories", str(trajectories),
             "--output", str(output), "--window", str(start), str(stop),
             "--seed", str(seed), "--renderer", "osmesa"],
            check=False,
        )
        if result.returncode:
            log = output / "rollout.log"
            if log.is_file():
                print(log.read_text(errors="replace")[-4000:], file=sys.stderr, flush=True)
            raise RuntimeError(f"PianoMime exited with code {result.returncode}")
        return {name: (output / name).read_bytes() for name in OUTPUTS}


@app.local_entrypoint()
def main(
    task: str = "SomewhereOnlyWeKnow_8",
    baseline: str = str(LAB / "baseline/trajectories"),
    candidate: str = str(LAB / "candidates/right-advance-6-8"),
    output: str = str(LAB / "modal-right-advance-6-8"),
    start: int = 120,
    stop: int = 160,
    seed: int = 0,
) -> None:
    sys.path.insert(0, str(SUBMISSION / "src"))
    from compare_rollouts import compare
    from trajectory_patch import HANDS, trajectory_name

    inputs = {
        label: {hand: (Path(path) / trajectory_name(task, hand)).read_bytes() for hand in HANDS}
        for label, path in (("baseline", baseline), ("candidate", candidate))
    }
    output_dir = Path(output)
    output_dir.mkdir(parents=True, exist_ok=False)
    calls = {name: rollout.spawn(task, hands, start, stop, seed) for name, hands in inputs.items()}
    for name, call in calls.items():
        print(f"{name}: {call.object_id}", flush=True)

    for name, call in calls.items():
        files = call.get()
        trial_dir = output_dir / name
        trial_dir.mkdir()
        for filename in OUTPUTS:
            (trial_dir / filename).write_bytes(files[filename])

    result = compare(output_dir / "baseline/trace.npz", output_dir / "candidate/trace.npz", start, stop)
    (output_dir / "comparison.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
