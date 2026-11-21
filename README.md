# PianoMime Rehearsal

Edit a local hand-trajectory window, replay it with PianoMime, and compare the realized key states with an earlier performance.

## Open the rehearsal studio

Open [the rehearsal studio](demo/index.html) in a desktop browser. On macOS, run `open demo/index.html` from this directory. The bundled 19.6-second recording plays locally and includes all three Gemini rounds, their actual decisions, score changes, and synchronized before/after passages. **Replay rehearsal** plays each tested passage in sequence and ends with the complete best take. Select a round or time window to explore it independently. Playback uses the packaged files and consumes zero GPU time.

The left video renders the target score on a piano roll. The right shows the synchronized robot recording. Press **Target piano** or **Robot** to replay the displayed passage from its start. Both buttons stop at the same endpoint. Seek to set a new listening start, inspect missed and extra pitches on the MIDI error strip, or download either MIDI track. Both MIDI files are reconstructed from the 50 ms evaluation key states.

To display a different completed engine run, install FFmpeg (including ffprobe), use a Python environment with NumPy and run `python demo/export_rehearsal.py /path/to/run --title "Clip title"` from this directory, then reload the page. The command replaces the bundled recording. Use `--output /path/to/staging` to export separately.

From the parent workspace, run `python3 submission/demo/watch_matrix.py` to follow the current four-strategy engine run, then open [Strategy progress](demo/progress.html). The local watcher checks the experiment folder every three seconds and exits after the engine writes its final summary. Pass a different matrix output directory as an argument to follow another run. A saved Modal call marks submission. The engine reports the next checkpoint after the rollout arrives.

## Project Structure

- `src/` contains the submission source code.
- [Project documentation](docs/PROJECT.md) covers the problem, solution, and demo walkthrough.

## Run on a GPU Host

Use a Linux host with a working PianoMime Python environment, CUDA, and FFmpeg. The runtime directory must contain the `pianomime/` checkout, the author's extracted `dataset/`, `checkpoint_ae.ckpt`, and `checkpoint_low_level.ckpt`. The checkout provides `pianomime/dataset_ll.zarr`. [Upstream installation and assets](https://github.com/sNiper-Qian/pianomime#installation-and-setup)

Create a candidate from a pair of high-level trajectories. The example advances the right hand by one frame within frames 120 through 159.

```bash
python src/trajectory_patch.py SomewhereOnlyWeKnow_8 \
  --source inputs/trajectories --output runs/candidate \
  --hand right --start-frame 120 --stop-frame 160 --offset -1
```

Replay the candidate and compare it with the baseline recorded on the same host configuration.

```bash
python src/run_rollout.py SomewhereOnlyWeKnow_8 \
  --runtime /path/to/pianomime-runtime --trajectories runs/candidate \
  --baseline-trace inputs/baseline_trace.npz --window 120 160 \
  --seed 0 --output runs/trial-001
```

Each run writes `performance.mp4`, `trace.npz`, `rollout.log`, and `result.json`. The comparison checks target keys and seed and reports whether the original full-clip F1 improved. To generate a baseline trace on the same host, use the original trajectories and omit `--baseline-trace`.

## Run the First Pair on Modal

From the parent workspace with the saved baseline and candidate trajectories, run this command in a terminal with a configured Modal account.

```bash
uv run --no-project --python 3.13 --with modal --with numpy modal run submission/modal_rollout.py
```

The first invocation builds the pinned PianoMime runtime and fetches its public assets. It then runs the original and candidate trajectories on two T4 workers using the same seed and software renderer. Outputs are downloaded to `experiments/pianomime/modal-right-advance-6-8/`. Use `--output` with a fresh path for another run. [Modal GPU documentation](https://modal.com/docs/guide/gpu)

## Propose a Correction With Gemini

From the parent workspace, set `GEMINI_APIKEY` in `.env`. Neon diagnoses the rollout and Hydrogen selects one supported timing adjustment. Pass the video and trace from the same rollout. Omit `--video` for the keys-only comparison.

```bash
python3.13 submission/src/gemini_proposal.py SomewhereOnlyWeKnow_8 \
  --source experiments/pianomime/baseline/trajectories \
  --trace experiments/pianomime/baseline/baseline_trace.npz \
  --video experiments/pianomime/baseline/baseline.mp4 \
  --start-frame 120 --stop-frame 160 \
  --output experiments/pianomime/candidates/gemini-video
```

The output contains the candidate trajectories and `proposal.json` with the diagnosis and selected operation. Pass the directory as `--candidate` to the Modal command with a fresh `--output` to simulate and score the correction. [Gemini video input](https://aistudio.google.com/docs/video-understanding#inline-video) · [Structured output](https://aistudio.google.com/docs/structured-output)

## Rehearse the Whole Clip

From the parent workspace, start both strategies from the same recorded baseline and matching hand trajectories. The command runs three candidate performances per strategy on Modal. Set `GEMINI_APIKEY` in the local `.env` file and configure the Modal CLI before running it.

```bash
uv run --no-project --python 3.11 --with modal==1.6.4 --with numpy==1.26.4 \
  python submission/rehearse.py \
  --baseline experiments/pianomime/modal-gemini-video/baseline \
  --trajectories experiments/pianomime/baseline/trajectories \
  --rounds 3 --output experiments/pianomime/whole-clip-rehearsal-01
```

Neon receives the full performance video with audio and the score and played keys for every 50 ms frame. It proposes edits at specific times and cites key errors. Hydrogen selects one edit to test. Both strategies can move either hand toward higher or lower keys by half or one white-key spacing, or advance or delay its horizontal preparation by one frame. Each operation preserves the reference height and score timing.

In `gemini_windows`, Neon chooses the passage. In `most_errors`, the program counts missed and extra keys in each two-second window and selects the earliest peak-error frame within the worst window. Neon chooses edit boundaries around that frame. Both strategies can propose ranges of 2 to 80 frames. Each trial replays the entire clip. A higher full-clip F1 retains the candidate as the next parent. The next diagnosis gets the latest retained performance and, following a rejected trial, the rejected video and key trace as well.

`protocol.json` identifies the common inputs and budget. Each round saves its model responses, candidate trajectories, recorded performance, key and action trace, and comparison against its parent. `summary.json` points to each strategy's best performance. Reuse the output directory to resume the same experiment or read its completed results. Use a fresh directory for an independent run.

On the 19.6-second development clip `SomewhereOnlyWeKnow_8` at seed 0, the baseline F1 was 0.577151. After three trials each, the model-selected strategy retained round 3 at 0.659400. The error-count strategy retained round 2 at 0.659867 after its third trial scored 0.645253.

## Compare Practice Time Budgets

Start four independent continuations of the completed error-count rehearsal from the parent workspace.

```bash
uv run --no-project --python 3.11 --with modal==1.6.4 --with numpy==1.26.4 \
  python submission/timed_rehearsal.py \
  --previous experiments/pianomime/whole-clip-rehearsal-01 \
  --minutes 15 20 25 30 --output experiments/pianomime/timed-most-errors-01
```

All branches inherit the same retained trajectories, performance, previous trial scores, and last rejected performance. They start on a common clock after the Modal app initializes. Each branch calls Gemini independently and uses a T4 worker for its physical replays. Its budget includes model calls, queue time, simulation, download, and scoring. An edit is eligible for retention when its verified full-clip score arrives before the cutoff. A late model response is discarded. An unfinished GPU call is cancelled at the cutoff.

Each budget directory records scored trials with elapsed time, the best performance path, and the stage reached at the deadline. The root `summary.json` collects the four outcomes. Resuming the same experiment uses its original deadlines.

## Compare four strategies by completed replays

Run from the parent workspace with the same Gemini key and Modal credentials as above.

```bash
uv run --no-project --python 3.11 --with modal==1.6.4 --with numpy==1.26.4 \
  python submission/practice_matrix.py --output experiments/pianomime/strategy-matrix-01
```

| Strategy | Passage selection | Edits per replay |
| --- | --- | ---: |
| A · Most errors | Select the two-second interval with the most missed and extra key-frames. | 1 |
| B · Gemini selects | Gemini selects a passage from the full performance. | 1 |
| C · Switch after a stall | Use A's rule. After two consecutive rejections in the same interval, skip it for one replay. | 1 |
| D · Gemini selects three | Gemini proposes disjoint passages and chooses a bundle of three actions to simulate together. | 3 |

Each strategy runs five independent branches from the original performance and empty trial history. The 15, 20, 25, 30 and 35 minute estimates correspond to 2, 3, 4, 5 and 6 completed replays. The twenty branches run concurrently, sharing up to nine T4 workers. Set `PIANOMIME_GPU_WORKERS` to adjust the worker pool for your workspace. Each branch waits for the assigned number of scored performances, including attempts that lower F1. A higher full-clip F1 retains the new trajectories. Strategy D makes one retention decision for the whole bundle.

Each branch's `results.json` includes the actual elapsed time, trial scores and retained performance. Each round saves the model decisions, edited trajectories, GPU call ID, video, trace and score. Reusing the same output directory resumes interrupted work. The final `summary.json` records all twenty branches and eighty scores.
