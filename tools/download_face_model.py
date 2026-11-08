"""Download Google's official local face model; never opens the camera."""
import _bootstrap  # Resolve application imports from any working directory.
from project_paths import PROJECT_ROOT
import hashlib
from pathlib import Path
import urllib.request
import zipfile

BASE = PROJECT_ROOT
URL = 'https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task'
SHA256 = '64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff'


def main():
    destination = BASE/'models'/'face_landmarker.task'
    destination.parent.mkdir(exist_ok=True)
    if destination.is_file():
        if hashlib.sha256(destination.read_bytes()).hexdigest() != SHA256:
            raise RuntimeError('Existing face model has an unexpected checksum. File was kept.')
        print('Face tracking model already exists. Existing file kept.')
        return
    pending = destination.with_suffix('.download')
    try:
        with urllib.request.urlopen(URL, timeout=60) as response, pending.open('xb') as output:
            while chunk := response.read(1024*1024):
                output.write(chunk)
        with zipfile.ZipFile(pending) as bundle:
            assert {'face_detector.tflite', 'face_landmarks_detector.tflite'}.issubset(bundle.namelist())
            assert bundle.testzip() is None
        if hashlib.sha256(pending.read_bytes()).hexdigest() != SHA256:
            raise RuntimeError('Face model checksum mismatch. Download was not installed.')
        pending.rename(destination)
        print('Model ready. SHA256:', hashlib.sha256(destination.read_bytes()).hexdigest())
    finally:
        pending.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
