"""Ask two Gemini models for one executable PianoMime trajectory correction."""

import argparse
import base64
from http.client import HTTPException, HTTPSConnection
import json
from pathlib import Path
import sys

from compare_rollouts import load_trace
from trajectory_patch import HANDS, write_candidate


DIAGNOSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "observations": {"type": "array", "items": {"type": "string"}},
        "hypothesis": {"type": "string"},
        "suspected_hand": {"type": "string", "enum": ["left", "right", "unknown"]},
    },
    "required": ["observations", "hypothesis", "suspected_hand"],
}
PROPOSAL_SCHEMA = {
    "type": "object",
    "properties": {
        "hand": {"type": "string", "enum": ["left", "right"]},
        "offset_frames": {"type": "integer", "enum": [-1, 1]},
        "rationale": {"type": "string"},
    },
    "required": ["hand", "offset_frames", "rationale"],
}


def api_key(env_file: Path) -> str:
    settings = {}
    for line in env_file.read_text(encoding="utf-8-sig").splitlines():
        name, separator, value = line.strip().removeprefix("export ").partition("=")
        if separator and not name.startswith("#"):
            settings[name.strip()] = value.split(" #", 1)[0].strip().strip("\"'")
    for name in ("GEMINI_APIKEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
        if settings.get(name):
            return settings[name]
    raise ValueError(f"No Gemini API key found in {env_file}")


def request_json(key: str, model: str, parts: list[dict], schema: dict, *, timeout: float = 90) -> tuple[dict, dict]:
    request = json.dumps({
        "model": model,
        "input": parts,
        "store": False,
        "response_format": {"type": "text", "mime_type": "application/json", "schema": schema},
    })
    connection = HTTPSConnection("generativelanguage.googleapis.com", timeout=timeout)
    try:
        connection.request("POST", "/v1beta/interactions", body=request, headers={
            "x-goog-api-key": key,
            "Content-Type": "application/json",
            "Api-Revision": "2026-05-20",
        })
        response = connection.getresponse()
        payload = json.load(response)
    except (OSError, HTTPException, ValueError) as error:
        raise RuntimeError(f"{model} request failed: {str(error).replace(key, '[redacted]')}") from None
    finally:
        connection.close()

    if response.status != 200:
        error = payload.get("error", {})
        message = str(error.get("message", response.reason)).replace(key, "[redacted]")
        raise RuntimeError(f"{model} returned HTTP {response.status}: {message[:300]}")
    if payload.get("status") != "completed":
        raise RuntimeError(f"{model} did not complete the interaction")
    text = "".join(
        part["text"] for step in payload.get("steps", []) if step.get("type") == "model_output"
        for part in step.get("content", []) if part.get("type") == "text"
    )
    try:
        result = json.loads(text)
    except ValueError:
        raise RuntimeError(f"{model} did not return a JSON object") from None
    if not isinstance(result, dict):
        raise RuntimeError(f"{model} did not return a JSON object")
    return result, {"model": model, "interaction_id": payload.get("id"), "usage": payload.get("usage")}


def propose(args: argparse.Namespace) -> dict:
    if args.diagnosis_model == args.planner_model:
        raise ValueError("Use distinct models for diagnosis and planning")
    if args.output.exists():
        raise FileExistsError(f"Candidate directory already exists: {args.output}")
    target, played, _, seed = load_trace(args.trace)
    if not 0 <= args.start_frame < args.stop_frame <= len(target) or args.stop_frame - args.start_frame < 2:
        raise ValueError("Select at least two frames within the rollout")

    frames = [
        {
            "frame": frame,
            "seconds": round(frame * 0.05, 3),
            "target_midi": (target[frame].nonzero()[0] + 21).tolist(),
            "played_midi": (played[frame].nonzero()[0] + 21).tolist(),
        }
        for frame in range(args.start_frame, args.stop_frame)
    ]
    evidence = json.dumps(frames, separators=(",", ":"))
    prompt = (
        "Diagnose one plausible performance failure in this PianoMime window. "
        "Each evaluation frame is 50 milliseconds. The video may include an initial reset frame. "
        "Distinguish the observed mismatch from your hypothesis about hand movement. "
        "Use the media when supplied and say when the responsible hand is uncertain. "
        "Cite the actual frame, time, or MIDI pitches in your observations. "
        f"Key-state evidence:\n{evidence}"
    )
    parts = [{"type": "text", "text": prompt}]
    if args.video:
        if args.video.stat().st_size > 14_000_000:
            raise ValueError("Use a smaller MP4 to fit the Gemini inline request limit")
        parts.append({"type": "video", "mime_type": "video/mp4",
                      "data": base64.b64encode(args.video.read_bytes()).decode("ascii")})

    key = api_key(args.env_file)
    print(f"Diagnosing with {args.diagnosis_model}", file=sys.stderr, flush=True)
    diagnosis, diagnosis_call = request_json(key, args.diagnosis_model, parts, DIAGNOSIS_SCHEMA)
    planning_prompt = (
        "Choose one testable timing correction for a simulated piano hand. "
        "Choose either left or right and one frame earlier (-1) or later (+1). "
        f"Apply only within frames [{args.start_frame}, {args.stop_frame}). "
        "The experiment will replay the clip and compare the original full-clip key-state F1. "
        "Explain why this is a useful probe given the diagnosis. "
        f"Diagnosis:\n{json.dumps(diagnosis)}"
    )
    print(f"Planning with {args.planner_model}", file=sys.stderr, flush=True)
    proposal, planner_call = request_json(
        key, args.planner_model, [{"type": "text", "text": planning_prompt}], PROPOSAL_SCHEMA,
    )
    hand = proposal.get("hand")
    offset = proposal.get("offset_frames")
    if hand not in HANDS or type(offset) is not int or offset not in (-1, 1):
        raise ValueError("Gemini proposed a timing change outside the supported operations")

    modification = write_candidate(
        args.source, args.output, args.task, hand, args.start_frame, args.stop_frame, offset,
    )
    result = {
        "task": args.task,
        "arm": "keys_and_video" if args.video else "keys_only",
        "seed": seed,
        "diagnosis_call": diagnosis_call,
        "diagnosis": diagnosis,
        "planner_call": planner_call,
        "proposal": proposal,
        "modification": modification,
    }
    (args.output / "proposal.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument("--video", type=Path, help="Optional MP4 of the same rollout")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--start-frame", type=int, required=True)
    parser.add_argument("--stop-frame", type=int, required=True)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--diagnosis-model", default="gemini-4-neon")
    parser.add_argument("--planner-model", default="gemini-4-hydrogen")
    try:
        result = propose(parser.parse_args())
    except (KeyError, OSError, ValueError, RuntimeError) as error:
        parser.exit(1, f"Proposal failed: {error}\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
