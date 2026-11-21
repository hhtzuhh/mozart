"""Choose a hand movement from the full audiovisual evidence for one rehearsal."""

import base64
import hashlib
from itertools import combinations
import json
from pathlib import Path
import time

import numpy as np

from compare_rollouts import load_trace
from gemini_proposal import request_json
from trajectory_patch import ADAPTIVE_MOTIONS, HANDS, HAND_MOTIONS, move_hand, trajectory_name


STRATEGIES = ("gemini_windows", "most_errors")
MATRIX_STRATEGIES = ("most_errors", "gemini_windows", "switch_on_stall", "gemini_three")
ADAPTIVE = "gemini_adaptive"
DIAGNOSER = "gemini-4-neon"
PLANNER = "gemini-4-hydrogen"
MENU = (
    "higher_half_key / lower_half_key translate the hand along the keyboard by 11.75 mm. "
    "higher_one_key / lower_one_key translate it by 23.5 mm (one white-key spacing). "
    "The lateral displacement ramps over the first and last four frames. "
    "prepare_earlier / prepare_later move its horizontal XY path one 50 ms frame earlier / later. "
    "All six options preserve the reference height on every frame. The trained controller "
    "recomputes joint actions; the simulator determines actual key contact."
)
IDEA_FIELDS = {
    "start_frame": {"type": "integer"}, "stop_frame": {"type": "integer"},
    "evidence_frame": {"type": "integer"}, "hand": {"type": "string", "enum": list(HANDS)},
    "motion": {"type": "string", "enum": list(HAND_MOTIONS)},
    "observation": {"type": "string"}, "hypothesis": {"type": "string"},
}
IDEAS_SCHEMA = {
    "type": "object",
    "properties": {"ideas": {"type": "array", "items": {
        "type": "object", "properties": IDEA_FIELDS, "required": list(IDEA_FIELDS),
    }}},
    "required": ["ideas"],
}


def adaptive_instructions(hands: dict, target: np.ndarray, played: np.ndarray) -> str:
    # Include one reference pose per mismatching run, keyed by the exact frame.
    poses = {}
    for start, _, goal, actual in key_runs(target, played):
        if goal != actual:
            poses[start] = {hand: np.rint(path[start, 1, 1:] * 1000).astype(int).tolist()
                            for hand, path in hands.items()}
    return (
        " Invent a practice tactic for each proposal. Give it a short descriptive tactic name. "
        "Use prior local and full-clip scores to decide whether to repeat, refine or switch tactics. "
        "In hypothesis say which pitches should change and which should be preserved. "
        "You can also shift one reference fingertip by 11.75 mm toward higher or lower notes "
        "with tip_1_higher through tip_5_lower. For a fingertip edit use a listed pose as "
        "evidence_frame. Each array gives lateral Y positions in mm for tips 1..5 in index order. "
        "Higher Y means higher pitch. These are reference poses, actual contacts are in the key trace. "
        f"Reference poses by frame: {json.dumps(poses, separators=(',', ':'))}. "
    )


def save_json(path: Path, data: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def key_runs(target: np.ndarray, played: np.ndarray) -> list:
    """Encode every 50 ms frame, combining adjacent identical score/performance pairs."""
    changes = np.any(target[1:] != target[:-1], axis=1) | np.any(played[1:] != played[:-1], axis=1)
    edges = [0, *(np.flatnonzero(changes) + 1).tolist(), len(target)]
    return [[a, b, (np.flatnonzero(target[a]) + 21).tolist(), (np.flatnonzero(played[a]) + 21).tolist()]
            for a, b in zip(edges, edges[1:])]


def worst_region(target: np.ndarray, played: np.ndarray, skip: int | None = None) -> dict:
    errors = np.count_nonzero(target != played, axis=1)
    totals = {s: int(errors[s:s + 40].sum()) for s in range(0, len(target), 40)}
    eligible = [s for s, total in totals.items() if s != skip and total > 0]
    start = max(eligible or totals, key=totals.get)
    stop = min(start + 40, len(target))
    anchor = start + int(np.argmax(errors[start:stop]))
    return {"start_frame": start, "stop_frame": stop, "anchor": anchor,
            "missed_plus_extra": int(errors[start:stop].sum())}


def select_region(strategy: str, target: np.ndarray, played: np.ndarray, history: list) -> dict | None:
    if strategy == "most_errors":
        return worst_region(target, played)
    if strategy != "switch_on_stall":
        return None
    skip = None
    if len(history) >= 2:
        first, second = history[-2:]
        if not first["adopted"] and not second["adopted"] and first["rule_start"] == second["rule_start"]:
            skip = second["rule_start"]
    return worst_region(target, played, skip)


def edit_id(edit: dict) -> tuple:
    return tuple(edit[k] for k in ("start_frame", "stop_frame", "hand", "motion"))


def eligible_triples(ideas: list, history: list, parent_hash: str) -> list:
    previous = {tuple(sorted(edit_id(e) for e in r["edits"]))
                for r in history if r["parent_id"] == parent_hash}
    bundles = []
    for group in combinations(range(len(ideas)), 3):
        edits = sorted(edit_id(ideas[i]) for i in group)
        if all(a[1] <= b[0] for a, b in zip(edits, edits[1:])) and tuple(edits) not in previous:
            bundles.append(group)
    return bundles


def video_part(path: Path) -> dict:
    if path.stat().st_size > 14_000_000:
        raise ValueError(f"Video exceeds the inline size limit: {path}")
    return {"type": "video", "mime_type": "video/mp4",
            "data": base64.b64encode(path.read_bytes()).decode("ascii")}


def seconds_left(deadline: float | None, maximum: float) -> float:
    if deadline is None:
        return maximum
    left = deadline - time.monotonic()
    if left <= 0:
        raise TimeoutError("Rehearsal time budget exhausted")
    return min(left, maximum)


def model_call(path: Path, key: str, model: str, prompt: str, media: list, schema: dict,
               deadline: float | None = None) -> dict:
    timeout = seconds_left(deadline, 420)
    if path.is_file():
        stored = json.loads(path.read_text())
        if stored["prompt"] != prompt or stored["model"] != model:
            raise ValueError(f"Saved model input differs for {path}")
        return stored["response"]
    print(f"{path.parent.parent.name}/{path.parent.name}: calling {model}", flush=True)
    response, meta = request_json(key, model, [{"type": "text", "text": prompt}, *media], schema, timeout=timeout)
    seconds_left(deadline, 420)
    save_json(path, {"model": model, "prompt": prompt, "response": response, "call": meta})
    return response


def make_plan(key: str, strategy: str, task: str, source: Path, performance: Path,
              directory: Path, history: list, feedback: Path | None = None, *, deadline: float | None = None) -> dict:
    target, played, _, seed = load_trace(performance / "trace.npz")
    hands = {hand: np.load(source / trajectory_name(task, hand), allow_pickle=False) for hand in HANDS}
    if any(array.shape != (len(target), 3, 6) for array in hands.values()):
        raise ValueError("The reference trajectories must match the performance length")
    adaptive = strategy == ADAPTIVE
    if strategy not in MATRIX_STRATEGIES and not adaptive:
        raise ValueError(f"Unknown window strategy: {strategy}")
    region = select_region(strategy, target, played, history)
    triple = strategy == "gemini_three"
    amount = "four or five ranked" if triple else "two or three ranked"
    selection = "Choose the most promising passages anywhere in the entire performance."
    motions = ADAPTIVE_MOTIONS if adaptive else HAND_MOTIONS
    schema = IDEAS_SCHEMA
    if adaptive:
        selection += adaptive_instructions(hands, target, played)
        fields = {**IDEA_FIELDS, "motion": {"type": "string", "enum": list(motions)},
                  "tactic": {"type": "string"}}
        schema = {"type": "object", "properties": {"ideas": {"type": "array", "items": {
            "type": "object", "properties": fields, "required": list(fields)}}}, "required": ["ideas"]}
    if triple:
        selection += (" One replay will apply exactly three edits at once. Propose four or five distinct "
                      "passages across the clip, including at least three mutually disjoint time ranges. "
                      "Choose a mismatching evidence frame in each. After a rejected bundle, propose "
                      "a different combination on the retained performance.")
    if region:
        selection = (f"The error-count rule selected frames [{region['start_frame']}, {region['stop_frame']}). "
                     f"Every proposed edit must contain frame {region['anchor']}, the first peak-error "
                     "frame in that region. You choose edit boundaries and movement.")
    parent_hash = hashlib.sha256((performance / "trace.npz").read_bytes()).hexdigest()
    directory.mkdir(parents=True, exist_ok=True)
    prompt = (
        f"Watch and listen to the entire {len(target) * 0.05:.2f} second robot piano performance in video 1. "
        f"The score and its timestamps are fixed. Diagnose physical mistakes and give {amount}, "
        "different, executable practice ideas with exact frame ranges. Describe what was observed and what "
        "is inferred. Cite target and played MIDI pitches at an actual mismatching evidence_frame within each "
        "range. Look for corroborating evidence in both media and key states. The video can have an initial "
        "reset frame. Each edit changes one hand within 2 to 80 frames. Indices are zero-based; stop_frame "
        f"is exclusive and at most {len(target)}. {selection} {MENU} "
        "Select a motion for each idea. Consider earlier attempts. Avoid repeating the same edit of "
        "the same parent. X, Y and Z are the simulation coordinates. Positive Y is toward higher pitches. "
        "Input rows losslessly represent all frames as [first_frame, exclusive_end, target_MIDI, played_MIDI]. "
        "Within a row every 50 ms frame has the indicated key states.\n"
        f"Current parent ID: {parent_hash}\nPrevious trials: {json.dumps(history, separators=(',', ':'))}\n"
        f"Video 1 score and performance: {json.dumps(key_runs(target, played), separators=(',', ':'))}"
    )
    media = [video_part(performance / "performance.mp4")]
    if feedback is not None:
        last_target, last_played, _, last_seed = load_trace(feedback / "trace.npz")
        if last_seed != seed or not np.array_equal(last_target, target):
            raise ValueError("Feedback must use the same score and seed")
        prompt += ("\nVideo 2 shows the last rejected trial. Learn from its outcome; edit video 1's "
                   "parent. Video 2 score and performance: "
                   + json.dumps(key_runs(target, last_played), separators=(",", ":")))
        media.append(video_part(feedback / "performance.mp4"))

    ideas = []
    tried = {edit_id(r) for r in history if r["parent_id"] == parent_hash and not triple}
    bundles = []
    for attempt in range(3 if triple else 2):
        diagnosis = model_call(directory / f"diagnosis-{attempt + 1}.json", key, DIAGNOSER, prompt, media, schema, deadline)
        for idea in diagnosis.get("ideas", [])[:5 if triple else 3]:
            a, b, e = (idea.get(k) for k in ("start_frame", "stop_frame", "evidence_frame"))
            hand, motion = idea.get("hand"), idea.get("motion")
            if not all(type(v) is int for v in (a, b, e)) or not (0 <= a <= e < b <= len(target) and 2 <= b-a <= 80):
                continue
            if adaptive and not (isinstance(idea.get("tactic"), str) and idea["tactic"].strip()):
                continue
            if hand not in hands or motion not in motions or np.array_equal(target[e], played[e]):
                continue
            if region and not a <= region["anchor"] < b:
                continue
            if (a, b, hand, motion) in tried or np.array_equal(move_hand(hands[hand], a, b, motion), hands[hand]):
                continue
            tried.add((a, b, hand, motion))
            ideas.append(idea)
        if triple:
            bundles = eligible_triples(ideas, history, parent_hash)
        if bundles or (ideas and not triple):
            break
        prompt += "\nProvide new eligible edits. Check the frame limits, anchor if supplied, movement, mismatch, and previous trials."
        if triple:
            prompt += f" Supply disjoint ranges that complement these eligible edits: {json.dumps(ideas)}"
    if not ideas or (triple and not bundles):
        raise ValueError("Gemini returned no new executable ideas for this rehearsal")
    options = [[i + 1 for i in group] for group in bundles] if triple else ideas
    choices = list(range(1, len(options) + 1))
    planner_schema = {"type": "object", "properties": {
        "choice": {"type": "integer", "enum": choices}, "rationale": {"type": "string"},
    }, "required": ["choice", "rationale"]}
    planning_prompt = (
        "Select one of the numbered edits to try in the simulator. Consider evidence, confidence and "
        "the tradeoff between the local error and full-clip F1. A candidate is retained only on strictly "
        "higher full-clip F1 than the current parent. Explain your choice briefly. "
        f"Prior trials: {json.dumps(history)}\n"
        f"Available edits: {json.dumps(dict(zip(choices, ideas)))}"
    )
    if triple:
        planning_prompt = (
            "Select one numbered bundle of exactly three disjoint hand edits to apply together in one "
            "physical replay. Choose the bundle most likely to increase full-clip F1. Assess their "
            "joint effect and prior trials. Explain your choice briefly. "
            f"Prior trials: {json.dumps(history)}\n"
            f"Edits by ID: {json.dumps(dict(enumerate(ideas, 1)))}\n"
            f"Available bundles of edit IDs: {json.dumps(dict(zip(choices, options)))}"
        )
    if adaptive:
        planning_prompt += ("\nChoose this round's tactic. Explain why the current evidence and previous "
                            "outcomes support keeping or changing the approach. Name the expected note-level effect.")
    selection = model_call(directory / "planner.json", key, PLANNER, planning_prompt, [], planner_schema, deadline)
    choice = selection.get("choice")
    if type(choice) is not int or choice not in choices:
        raise ValueError("Planner selected an unavailable edit")
    plan = {"strategy": strategy, "seed": seed, "parent_id": parent_hash, "rule_region": region,
            "frames": len(target), "ideas": ideas, "choice": choice,
            "rationale": selection["rationale"]}
    if triple:
        plan.update(bundles=options, selected_edits=[ideas[i] for i in bundles[choice-1]])
    else:
        plan["selected"] = ideas[choice-1]
    save_json(directory / "proposal.json", plan)
    return plan
