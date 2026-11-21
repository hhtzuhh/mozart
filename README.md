# Mozart · A piano rehearsal agent

Mozart helps a simulated pianist practice a difficult passage. Gemini watches the performance, compares the played notes with the score, and chooses a small change to a hand movement. PianoMime plays the entire clip again on a Modal GPU. The agent keeps the change only if the new performance scores higher.

## Open the rehearsal studio

Open [the rehearsal studio](demo/index.html) in a desktop browser. On macOS, run `open demo/index.html` from this directory. The bundled 19.6-second recording plays locally and includes all three Gemini rounds, their actual decisions, score changes, and synchronized before/after passages. **Replay rehearsal** plays each tested passage in sequence and ends with the complete best take. Select a round or time window to explore it independently. Playback uses the packaged files and consumes zero GPU time.

The left video renders the target score on a piano roll. The right shows the synchronized robot recording. Press **Target piano** or **Robot** to replay the displayed passage from its start. Both buttons stop at the same endpoint. Seek to set a new listening start, inspect missed and extra pitches on the MIDI error strip, or download either MIDI track. Both MIDI files are reconstructed from the 50 ms evaluation key states.

To display a different completed engine run, install FFmpeg (including ffprobe), use a Python environment with NumPy and run `python demo/export_rehearsal.py /path/to/run --title "Clip title"` from this directory, then reload the page. The command replaces the bundled recording. Use `--output /path/to/staging` to export separately.

## What changed after practice?

In the first recorded round, Gemini identified a left-hand chord landing one white key too low. The agent shifted the left hand toward higher notes for frames 114–152. In the studio, select round 1 and compare the same passage before and after the edit. Full-clip F1 rose from **0.5772 to 0.6314** on that replay.

| Performance | Best full-clip F1 | Retained take |
| --- | ---: | --- |
| Original | 0.5772 | Baseline |
| Gemini chooses the passage | 0.6594 | Round 3 |
| Error count chooses the passage | 0.6599 | Round 2 |

Both strategies started from the same 19.6-second clip, `SomewhereOnlyWeKnow_8`, at seed 0 and tested three edits each. F1 compares target and played keys every 50 ms, counting both missing and extra notes, then averages the frame scores across the clip. The studio shows the Gemini-selected rounds.

## Run the agent from a clone

Run the commands below from the directory containing this README. The offline studio includes its own media. For a new rehearsal, supply the original left and right hand trajectory arrays for the task. The filenames end in `_left_hand_action_list.npy` and `_right_hand_action_list.npy`. Set a shell variable to the directory containing both files. Each example uses the same shell.

```bash
TRAJECTORIES=/absolute/path/to/your/trajectories
```

For Gemini calls, create a local `.env` in the repository root containing `GEMINI_APIKEY=your-key`. If your key file is elsewhere, pass its path with `--env-file`. The default looks in the directory where you start Python. Use a Gemini key with access to the configured Neon and Hydrogen models. Modal uses its own configured CLI credentials. The examples pass data paths explicitly and store generated files in the ignored `runs/` directory.

The rehearsal loop is in `rehearse.py`, Gemini diagnosis and planning are in `src/rehearsal_plan.py`, and the T4 worker is in `modal_rollout.py`.

## Run on a GPU Host

Use a Linux host with a working PianoMime Python environment, CUDA, and FFmpeg. The runtime directory must contain the `pianomime/` checkout, the author's extracted `dataset/`, `checkpoint_ae.ckpt`, and `checkpoint_low_level.ckpt`. The checkout provides `pianomime/dataset_ll.zarr`. [Upstream installation and assets](https://github.com/sNiper-Qian/pianomime#installation-and-setup)

Create a candidate from a pair of high-level trajectories. The example advances the right hand by one frame within frames 120 through 159.

```bash
python src/trajectory_patch.py SomewhereOnlyWeKnow_8 \
  --source "$TRAJECTORIES" --output runs/candidate \
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

Create the local candidate with the trajectory patch command above, then run the pair with a configured Modal account. The candidate preparation requires Python and NumPy. The remote run requires uv locally and uses two T4 workers.

```bash
uv run --no-project --python 3.11 --with modal==1.6.4 --with numpy==1.26.4 \
  modal run modal_rollout.py --baseline "$TRAJECTORIES" \
  --candidate runs/candidate --output runs/first-pair
```

The first invocation builds the pinned PianoMime runtime and fetches its public assets. It then runs the original and candidate trajectories on two T4 workers using the same seed and software renderer. Outputs are downloaded to `runs/first-pair/`. Use `--output` with a fresh path for another run. [Modal GPU documentation](https://modal.com/docs/guide/gpu)

## Propose a Correction With Gemini

Neon diagnoses the rollout and Hydrogen selects one supported timing adjustment. Pass the video and trace from the same rollout. Omit `--video` for the keys-only comparison.

```bash
python src/gemini_proposal.py SomewhereOnlyWeKnow_8 \
  --source "$TRAJECTORIES" \
  --trace runs/first-pair/baseline/trace.npz \
  --video runs/first-pair/baseline/performance.mp4 \
  --start-frame 120 --stop-frame 160 \
  --output runs/gemini-video
```

The output contains the candidate trajectories and `proposal.json` with the diagnosis and selected operation. Pass the directory as `--candidate` to the Modal command with a fresh `--output` to simulate and score the correction. [Gemini video input](https://aistudio.google.com/docs/video-understanding#inline-video) · [Structured output](https://aistudio.google.com/docs/structured-output)

## Rehearse the Whole Clip

Start both strategies from the baseline generated by the Modal pair above, or pass an existing baseline directory containing matching `trace.npz`, `result.json`, and `performance.mp4` files. The command runs three candidate performances per strategy on Modal. Set `GEMINI_APIKEY` in the local `.env` file and configure the Modal CLI before running it.

```bash
uv run --no-project --python 3.11 --with modal==1.6.4 --with numpy==1.26.4 \
  python rehearse.py \
  --baseline runs/first-pair/baseline \
  --trajectories "$TRAJECTORIES" \
  --env-file .env --rounds 3 --output runs/whole-clip-rehearsal-01
```

Neon receives the full performance video with audio and the score and played keys for every 50 ms frame. It proposes edits at specific times and cites key errors. Hydrogen selects one edit to test. Both strategies can move either hand toward higher or lower keys by half or one white-key spacing, or advance or delay its horizontal preparation by one frame. Each operation preserves the reference height and score timing.

In `gemini_windows`, Neon chooses the passage. In `most_errors`, the program counts missed and extra keys in each two-second window and selects the earliest peak-error frame within the worst window. Neon chooses edit boundaries around that frame. Both strategies can propose ranges of 2 to 80 frames. Each trial replays the entire clip. A higher full-clip F1 retains the candidate as the next parent. The next diagnosis gets the latest retained performance and, following a rejected trial, the rejected video and key trace as well.

`protocol.json` identifies the common inputs and budget. Each round saves its model responses, candidate trajectories, recorded performance, key and action trace, and comparison against its parent. `summary.json` points to each strategy's best performance. Reuse the output directory to resume the same experiment or read its completed results. Use a fresh directory for an independent run.

On the 19.6-second development clip `SomewhereOnlyWeKnow_8` at seed 0, the baseline F1 was 0.577151. After three trials each, the model-selected strategy retained round 3 at 0.659400. The error-count strategy retained round 2 at 0.659867 after its third trial scored 0.645253.

## Compare Practice Time Budgets

Start four independent continuations of the completed error-count rehearsal.

```bash
uv run --no-project --python 3.11 --with modal==1.6.4 --with numpy==1.26.4 \
  python timed_rehearsal.py \
  --previous runs/whole-clip-rehearsal-01 \
  --initial-trajectories "$TRAJECTORIES" --env-file .env \
  --minutes 15 20 25 30 --output runs/timed-most-errors-01
```

All branches inherit the same retained trajectories, performance, previous trial scores, and last rejected performance. They start on a common clock after the Modal app initializes. Each branch calls Gemini independently and uses a T4 worker for its physical replays. Its budget includes model calls, queue time, simulation, download, and scoring. An edit is eligible for retention when its verified full-clip score arrives before the cutoff. A late model response is discarded. An unfinished GPU call is cancelled at the cutoff.

Each budget directory records scored trials with elapsed time, the best performance path, and the stage reached at the deadline. The root `summary.json` collects the four outcomes. Resuming the same experiment uses its original deadlines.

## Compare four strategies by completed replays

Use the same baseline, trajectories, Gemini key, and Modal credentials as above.

```bash
uv run --no-project --python 3.11 --with modal==1.6.4 --with numpy==1.26.4 \
  python practice_matrix.py --baseline runs/first-pair/baseline \
  --trajectories "$TRAJECTORIES" --env-file .env --output runs/strategy-matrix-01
```

| Strategy | Passage selection | Edits per replay |
| --- | --- | ---: |
| A · Most errors | Select the two-second interval with the most missed and extra key-frames. | 1 |
| B · Gemini selects | Gemini selects a passage from the full performance. | 1 |
| C · Switch after a stall | Use A's rule. After two consecutive rejections in the same interval, skip it for one replay. | 1 |
| D · Gemini selects three | Gemini proposes disjoint passages and chooses a bundle of three actions to simulate together. | 3 |

Each strategy runs five independent branches from the original performance and empty trial history. The 15, 20, 25, 30 and 35 minute estimates correspond to 2, 3, 4, 5 and 6 completed replays. The twenty branches run concurrently, sharing up to nine T4 workers. Set `PIANOMIME_GPU_WORKERS` to adjust the worker pool for your workspace. Each branch waits for the assigned number of scored performances, including attempts that lower F1. A higher full-clip F1 retains the new trajectories. Strategy D makes one retention decision for the whole bundle.

Each branch's `results.json` includes the actual elapsed time, trial scores and retained performance. Each round saves the model decisions, edited trajectories, GPU call ID, video, trace and score. Reusing the same output directory resumes interrupted work. The final `summary.json` records all twenty branches and eighty scores.

While the matrix runs, start `python3 demo/watch_matrix.py runs/strategy-matrix-01` in a second terminal from the repository root and open [Strategy progress](demo/progress.html). The watcher reads local checkpoints every three seconds and exits when the final summary appears.
