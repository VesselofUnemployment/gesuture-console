"""Download the two pretrained face models from OpenCV's official repository."""
import _bootstrap  # Resolve application imports from any working directory.
from project_paths import PROJECT_ROOT
import hashlib
import urllib.request
from pathlib import Path

BASE = PROJECT_ROOT
REVISION = '48b10fa505aeb51fbd127fc60a5c660040e823b7'
MODELS = (
    ("face_detection_yunet", "face_detection_yunet_2023mar.onnx"),
    ("face_recognition_sface", "face_recognition_sface_2021dec.onnx"),
)


def main():
    folder = BASE / "models"
    folder.mkdir(exist_ok=True)
    for group, filename in MODELS:
        relative = f"models/{group}/{filename}"
        pointer_url = f"https://raw.githubusercontent.com/opencv/opencv_zoo/{REVISION}/{relative}"
        with urllib.request.urlopen(pointer_url, timeout=60) as response:
            pointer = response.read().decode("ascii")
        values = dict(line.split(" ", 1) for line in pointer.splitlines())
        expected_hash = values["oid"].split(":", 1)[1]
        expected_size = int(values["size"])
        destination = folder / filename
        if destination.exists():
            digest = hashlib.sha256(destination.read_bytes()).hexdigest()
            if digest == expected_hash:
                print(f"Model already available: {filename}")
                continue
            raise RuntimeError(f"Unexpected checksum for the existing model: {destination}")
        temporary = destination.with_suffix(".download")
        print(f"Downloading model: {filename}", flush=True)
        try:
            url = f"https://media.githubusercontent.com/media/opencv/opencv_zoo/{REVISION}/{relative}"
            with urllib.request.urlopen(url, timeout=60) as response, temporary.open("wb") as output:
                while chunk := response.read(1024 * 1024):
                    output.write(chunk)
            if temporary.stat().st_size != expected_size:
                raise RuntimeError("The model download is incomplete.")
            if hashlib.sha256(temporary.read_bytes()).hexdigest() != expected_hash:
                raise RuntimeError("The model checksum did not match.")
            temporary.rename(destination)
            print(f"Ready: {filename}", flush=True)
        finally:
            temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
