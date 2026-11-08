"""Install the pinned official Google gesture bundle; never opens a camera."""
import _bootstrap  # Resolve application imports from any working directory.
from project_paths import PROJECT_ROOT
import hashlib
import os
from pathlib import Path
import subprocess
import urllib.error
import urllib.request
import zipfile

BASE = PROJECT_ROOT
URL = 'https://storage.googleapis.com/mediapipe-models/gesture_recognizer/gesture_recognizer/float16/1/gesture_recognizer.task'
SHA256 = '97952348cf6a6a4915c2ea1496b4b37ebabc50cbbf80571435643c455f2b0482'


def main():
    destination = BASE/'models'/'gesture_recognizer.task'
    destination.parent.mkdir(exist_ok=True)
    if destination.is_file():
        if hashlib.sha256(destination.read_bytes()).hexdigest() != SHA256:
            raise RuntimeError('Existing hand model has an unexpected checksum. File was kept.')
        print('Hand model already ready. Existing file kept.')
        return
    pending = destination.with_suffix('.download')
    # Reserve our temporary file; do not overwrite a previous partial download.
    with pending.open('xb'):
        pass
    try:
        try:
            with urllib.request.urlopen(URL, timeout=45) as response, pending.open('wb') as output:
                while chunk := response.read(1024*1024):
                    output.write(chunk)
        except urllib.error.URLError:
            if os.name != 'nt': raise
            # Some Windows Python SSL runtimes cannot use the machine's HTTPS path.
            # Use Windows HTTPS, with certificate validation still enabled.
            environment = os.environ.copy()
            environment['VISION_HAND_DOWNLOAD'] = str(pending)
            script = ("$ErrorActionPreference='Stop'; "
                      "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; "
                      "Invoke-WebRequest -UseBasicParsing -TimeoutSec 45 -Uri '"+URL+
                      "' -OutFile $env:VISION_HAND_DOWNLOAD")
            subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',script],
                           env=environment, check=True, timeout=55,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        if hashlib.sha256(pending.read_bytes()).hexdigest() != SHA256:
            raise RuntimeError('Hand model checksum mismatch; download was not installed.')
        with zipfile.ZipFile(pending) as bundle:
            if not {'hand_landmarker.task','hand_gesture_recognizer.task'}.issubset(bundle.namelist()) or bundle.testzip():
                raise RuntimeError('Invalid hand model bundle.')
        pending.rename(destination)
        print('Hand tracking model ready.')
    finally:
        pending.unlink(missing_ok=True)


if __name__ == '__main__': main()
