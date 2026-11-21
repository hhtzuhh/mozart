"""Fetch the public PianoMime archives used for the reproducible GPU runtime."""

import hashlib
from pathlib import Path, PurePosixPath
import subprocess
import sys
from tempfile import TemporaryDirectory
from zipfile import ZipFile


ARCHIVES = (
    ("1X8q-PvqyqL2X15wCZevTfAtSDfiHpYAa", "3317301ecb66a9b16a98246faf825c8f5316065da8b0c86ea1c028a8917c7211"),
    ("1-wa1UAn_mbPN87D6GIi4PS0VNDE5mbQh", "c848d4287608326841de302ccb3ad13ff3fc4e4d35ed77f7b3a6536c14f92336"),
)
CHECKPOINTS = {"checkpoint_ae.ckpt", "checkpoint_high_level.ckpt", "checkpoint_low_level.ckpt"}


def main(runtime: Path) -> None:
    runtime.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory() as temp:
        archive = Path(temp) / "asset.zip"
        for file_id, expected_hash in ARCHIVES:
            subprocess.run(["gdown", file_id, "--output", str(archive)], check=True)
            with archive.open("rb") as file:
                if hashlib.file_digest(file, "sha256").hexdigest() != expected_hash:
                    raise RuntimeError(f"PianoMime archive checksum mismatch for {file_id}")
            with ZipFile(archive) as package:
                for info in package.infolist():
                    member = PurePosixPath(info.filename)
                    if member.is_absolute() or ".." in member.parts:
                        raise ValueError(f"Unsafe archive path: {info.filename}")
                    if str(member) in CHECKPOINTS or member.parts[:1] == ("dataset",):
                        package.extract(info, runtime)
            archive.unlink()

    for filename in CHECKPOINTS:
        if not (runtime / filename).is_file():
            raise FileNotFoundError(f"Missing {filename} after extraction")
    if not (runtime / "dataset/notes_test").is_dir():
        raise FileNotFoundError("The extracted dataset has no notes_test directory")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
