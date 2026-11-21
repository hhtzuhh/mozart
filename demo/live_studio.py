"""Serve the rehearsal studio on localhost and launch independent, persistent live runs."""
import argparse
import json
import os
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
import traceback
from urllib.parse import urlsplit

from watch_matrix import DEMO, branch_state, read

PROJECT = DEMO.parent
RUNS = PROJECT / "runs/live"
SONG = "SomewhereOnlyWeKnow_8"
STRATEGIES = ("most_errors", "gemini_windows", "switch_on_stall", "gemini_three", "gemini_adaptive")
RUN_ID = r"[0-9a-f]{32}"
launch_lock = threading.Lock()


def write(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value) + "\n")
    temporary.replace(path)


def worker(folder):
    config = read(folder / "request.json")
    from personal_replay import follow
    done = threading.Event()
    packaging = threading.Thread(target=follow, args=(folder, done), daemon=True)
    packaging.start()
    try:
        sys.path.insert(0, str(PROJECT))
        from practice_matrix import main as rehearse
        rehearse(["--task", config["song"], "--strategy", config["strategy"], "--rounds", str(config["rounds"]),
                  "--env-file", config["env_file"], "--output", str(folder)])
    except BaseException as error:
        traceback.print_exc()
        write(folder / "failure.json", {"type": type(error).__name__})
        return 1
    finally:
        done.set()
        packaging.join()
    return 0


def start(payload, env_file):
    if not isinstance(payload, dict) or not isinstance(payload.get("id"), str) or not re.fullmatch(RUN_ID, payload["id"]):
        raise ValueError("Invalid rehearsal ID")
    song, strategy, rounds = (payload.get(k) for k in ("song", "strategy", "rounds"))
    if song != SONG or strategy not in STRATEGIES or type(rounds) is not int or not 1 <= rounds <= 6:
        raise ValueError("Choose the available song, a strategy and one to six rounds")
    folder = RUNS / payload["id"]
    settings = dict(song=song, strategy=strategy, rounds=rounds)
    with launch_lock:
        existing = read(folder / "request.json")
        if existing:
            if any(existing[k] != v for k, v in settings.items()):
                raise ValueError("This ID already refers to another rehearsal")
            return folder
        folder.mkdir(parents=True)
        write(folder / "request.json", dict(settings, created=time.time(), env_file=str(env_file)))
        try:
            with (folder / "worker.log").open("ab") as log:
                process = subprocess.Popen([sys.executable, "-u", __file__, "--worker", folder.name],
                                           stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                           start_new_session=True)
            write(folder / "process.json", {"pid": process.pid})
            threading.Thread(target=process.wait, daemon=True).start()
        except OSError as error:
            write(folder / "failure.json", {"type": type(error).__name__})
            raise
    return folder


def status(folder):
    config = read(folder / "request.json")
    if not config:
        return None
    strategy, rounds = config["strategy"], config["rounds"]
    protocol = read(folder / "protocol.json", {})
    group = folder / strategy / f"{5 * (rounds + 1)}min"
    state = branch_state(group, rounds, protocol.get("baseline_f1"))
    for trial in state["history"]:
        if trial["video"]:
            trial["video"] = f"/api/runs/{folder.name}/round/{trial['number']}/video"
    failure = read(folder / "failure.json")
    process = read(folder / "process.json")
    if not failure and state["scored"] < rounds and process:
        try:
            os.kill(process["pid"], 0)
        except ProcessLookupError:
            failure = {"type": "Worker interrupted"}
        except PermissionError:
            # A worker launched by another session can still write its round results.
            pass
    if failure:
        state["phase"] = f"Stopped · {failure['type']} (see worker.log)"
    elif not (group / "start.json").is_file():
        state["phase"] = "Connecting to Modal" if protocol else "Starting rehearsal engine"
    return dict(state, id=folder.name, song=config["song"], strategy=strategy, rounds=rounds,
                modalAppUrl=modal_dashboard(folder),
                created=config["created"], failed=bool(failure))


def modal_dashboard(folder):
    # The SDK prints the App URL as soon as the local worker connects, before its first GPU call.
    try:
        with (folder / "worker.log").open("rb") as log:
            preamble = log.read(4096)
    except FileNotFoundError:
        return None
    match = re.search(rb"https://modal\.com/apps/[\w.-]+/[\w.-]+/ap-[A-Za-z0-9]+", preamble)
    return match[0].decode("ascii") if match else None


class Studio(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(DEMO), **kwargs)

    def json_response(self, code, value, *, local_discovery=False):
        body = json.dumps(value).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        origin = self.headers.get("Origin", "")
        if local_discovery and re.fullmatch(r"http://(?:127\.0\.0\.1|localhost)(?::[0-9]+)?", origin):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path != "/api/runs":
            return self.send_error(404)
        allowed = {f"{host}:{self.server.server_port}" for host in ("localhost", "127.0.0.1")}
        host = self.headers.get("Host")
        if host not in allowed or self.headers.get("Origin") != f"http://{host}":
            return self.send_error(403)
        try:
            size = int(self.headers.get("Content-Length", 0))
            if not 0 < size <= 4096 or self.headers.get_content_type() != "application/json":
                raise ValueError("Expected a small JSON rehearsal request")
            folder = start(json.loads(self.rfile.read(size)), self.server.env_file)
        except (ValueError, UnicodeError) as error:
            return self.json_response(400, {"error": str(error)})
        except OSError:
            return self.json_response(500, {"error": "Could not launch the local rehearsal worker"})
        self.json_response(202, {"id": folder.name})

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/api/health":
            return self.json_response(200, {"service": "mozart-live"}, local_discovery=True)
        if path == "/api/runs":
            rows = [status(f.parent) for f in RUNS.glob("*/request.json")]
            return self.json_response(200, sorted(rows, key=lambda r: r["created"], reverse=True))
        match = re.fullmatch(fr"/api/runs/({RUN_ID})/round/([1-6])/video", path)
        if match:
            folder = RUNS / match[1]
            cfg = read(folder / "request.json")
            if not cfg:
                return self.send_error(404)
            return self.video(folder / cfg["strategy"] / f"{5 * (cfg['rounds'] + 1)}min" /
                              f"round-{match[2]}" / "performance/performance.mp4")
        # The progress page also links directly to archived engine recordings.
        if re.fullmatch(r"/experiments/pianomime/[\w-]+/[\w-]+/\d+min/round-\d+/performance/performance.mp4", path):
            return self.video(PROJECT.parent / path.lstrip("/"))
        if path.startswith("/api/"):
            return self.send_error(404)
        if path.endswith(".mp4"):
            return self.video(Path(self.translate_path(self.path)))
        super().do_GET()

    def video(self, path):
        if not path.is_file():
            return self.send_error(404)
        with path.open("rb") as stream:
            size = os.fstat(stream.fileno()).st_size
            start, stop = 0, size
            requested = self.headers.get("Range")
            if requested:
                span = re.fullmatch(r"bytes=(\d*)-(\d*)", requested)
                if span and any(span.groups()):
                    if span[1]:
                        start = int(span[1])
                        if span[2]:
                            stop = min(size, int(span[2]) + 1)
                    else:
                        start = max(0, size - int(span[2]))
                if not span or not any(span.groups()) or start >= stop:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.end_headers()
                    return
            self.send_response(206 if requested else 200)
            self.send_header("Accept-Ranges", "bytes")
            if requested:
                self.send_header("Content-Range", f"bytes {start}-{stop-1}/{size}")
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(stop - start))
            self.end_headers()
            stream.seek(start)
            remaining = stop - start
            while remaining:
                chunk = stream.read(min(remaining, 128 * 1024))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8794)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--worker", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        if not re.fullmatch(RUN_ID, args.worker):
            parser.error("Invalid run ID")
        sys.exit(worker(RUNS / args.worker))
    env_file = args.env_file or next((p for p in [Path.cwd()/".env", PROJECT/".env", PROJECT.parent/".env"]
                                      if p.is_file()), Path.cwd()/".env")
    with ThreadingHTTPServer(("127.0.0.1", args.port), Studio) as server:
        server.env_file = env_file.resolve()
        print(f"Rehearsal studio: http://127.0.0.1:{server.server_port}/live.html", flush=True)
        server.serve_forever()
