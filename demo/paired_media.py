"""Export aligned target-piano and robot footage on a single media clock."""
from fractions import Fraction
import json
from pathlib import Path
import struct
import subprocess
from tempfile import TemporaryDirectory

import numpy as np


def variable_length(value):
    encoded = [value & 127]
    value >>= 7
    while value:
        encoded.append(128 | (value & 127))
        value >>= 7
    return bytes(reversed(encoded))


def write_midi(path, spans, frames, step):
    # 500 ticks per beat at 120 BPM gives one tick per millisecond.
    tick = lambda frame: round(frame * step * 1000)
    events = []
    for start, stop, pitch in spans:
        events.extend([(tick(start), 0x90, pitch, 80), (tick(stop), 0x80, pitch, 0)])
    track = bytearray(b"\x00\xff\x51\x03\x07\xa1\x20\x00\xc0\x00")
    last = 0
    for at, status, pitch, velocity in sorted(events):
        track.extend(variable_length(at - last) + bytes([status, pitch, velocity]))
        last = at
    track.extend(variable_length(tick(frames) - last) + b"\xff\x2f\x00")
    path.write_bytes(b"MThd" + struct.pack(">IHHH", 6, 0, 1, 500) + b"MTrk" + struct.pack(">I", len(track)) + track)


def piano_frames(target, spans, step):
    width, height, key_top = 480, 360, 274
    used = [note for _, _, note in spans] or [60]
    low = max(21, (min(used) // 12) * 12)
    high = min(108, (max(used) // 12 + 1) * 12 - 1)
    white = [n for n in range(low, high + 1) if n % 12 in (0, 2, 4, 5, 7, 9, 11)]
    key_width = width / len(white)
    boxes = {n: (round(i * key_width), round((i + 1) * key_width)) for i, n in enumerate(white)}
    black = [n for n in range(low, high + 1) if n not in boxes]
    for n in black:
        center = boxes[n - 1][1]
        boxes[n] = (round(center - .29 * key_width), round(center + .29 * key_width))
    horizon = max(1, round(2.5 / step))
    for frame, pressed in enumerate(target):
        img = np.empty((height, width, 3), dtype=np.uint8)
        img[:] = (26, 43, 42)
        for n in white:
            x, _ = boxes[n]
            img[:key_top, x:x+1] = (50, 74, 68)
        for start, stop, n in spans:
            if start > frame + horizon or stop <= frame:
                continue
            x, right = boxes[n]
            y = max(0, round(key_top * (1 - (stop - frame) / horizon)))
            bottom = min(key_top, round(key_top * (1 - (start - frame) / horizon)))
            img[y:bottom, x+1:right-1] = (132, 223, 172) if start <= frame else (89, 153, 128)
        for n in white:
            x, right = boxes[n]
            img[key_top:, x:right-1] = (124, 227, 153) if pressed[n - 21] else (232, 237, 228)
        for n in black:
            x, right = boxes[n]
            img[key_top:key_top+53, x:right] = (71, 187, 110) if pressed[n - 21] else (22, 33, 33)
        yield img


def ffmpeg(*args):
    return ["ffmpeg", "-v", "error", "-y", *map(str, args)]


def render_reference(path, target, spans, step, audio):
    fps = str(Fraction(1 / step).limit_denominator(10000))
    args = ffmpeg("-f", "rawvideo", "-pixel_format", "rgb24", "-video_size", "480x360",
                  "-framerate", fps, "-i", "pipe:0", "-i", audio, "-c:v", "libx264",
                  "-crf", 18, "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart", path)
    with subprocess.Popen(args, stdin=subprocess.PIPE) as encoder:
        try:
            for image in piano_frames(target, spans, step):
                encoder.stdin.write(image.tobytes())
        finally:
            encoder.stdin.close()
        if encoder.wait():
            raise RuntimeError("Could not encode the score reference")


def render_pair(reference, robot, dest, number, frames, step):
    stream = json.loads(subprocess.check_output([
        "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
        "stream=nb_frames,avg_frame_rate", "-of", "json", str(robot)]))["streams"][0]
    reset = int(stream["nb_frames"]) - frames
    if reset not in (0, 1) or abs(float(Fraction(stream["avg_frame_rate"])) * step - 1) > .0001:
        raise ValueError(f"Video and trace frame clocks differ in {robot}")
    # Recorder can include the pre-action reset image. Robot audio starts on the score clock.
    graph = (f"[1:v]trim=start_frame={reset},setpts=PTS-STARTPTS,scale=480:360,setsar=1[right];"
             "[0:v][right]hstack=inputs=2[v]")
    robot_mix = dest / f"take-{number}.mp4"
    with TemporaryDirectory(dir=dest) as temp:
        pair = Path(temp) / "paired.mp4"
        subprocess.run(ffmpeg("-i", reference, "-i", robot, "-filter_complex", graph,
                              "-map", "[v]", "-map", "1:a:0", "-c:v", "libx264", "-crf", 18,
                              "-pix_fmt", "yuv420p", "-c:a", "aac", "-t", frames * step,
                              "-movflags", "+faststart", pair), check=True)
        # Both audio choices carry the exact same encoded picture and time base.
        subprocess.run(ffmpeg("-i", pair, "-i", reference, "-map", "0:v:0", "-map", "1:a:0",
                              "-c", "copy", "-movflags", "+faststart", dest / f"take-{number}-score.mp4"), check=True)
        pair.replace(robot_mix)
