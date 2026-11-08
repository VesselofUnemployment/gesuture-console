"""Very quiet local PCM cues. Playback is asynchronous and never blocks vision."""
from project_paths import PROJECT_ROOT
from pathlib import Path
import time
try:
    import winsound
except ImportError:
    winsound=None

SOUND_DIR=PROJECT_ROOT/'assets'/'sounds'


class SoftSounds:
    def __init__(self, directory=SOUND_DIR):
        self.directory=Path(directory)
        self.enabled=True
        self.available=winsound is not None
        self.last_played=float('-inf')
        self.identity=None
        self.stage=-1
        self.completed=False
        self.scanning=False

    def set_enabled(self, enabled):
        self.enabled=bool(enabled)
        if not self.enabled:self.stop()

    def stop(self):
        if winsound is not None:
            try:winsound.PlaySound(None,0)
            except RuntimeError:pass

    def play(self, name, now=None):
        now=time.monotonic() if now is None else now
        path=self.directory/(name+'.wav')
        if not self.enabled or not self.available or not path.is_file() or now-self.last_played<.09:
            return
        try:
            winsound.PlaySound(str(path),winsound.SND_FILENAME|winsound.SND_ASYNC|winsound.SND_NODEFAULT)
            self.last_played=now
        except (RuntimeError,OSError):self.available=False

    def observe(self, identity, phase, fraction=0., now=None):
        if phase=='IDLE':
            self.identity=None;self.stage=-1;self.completed=False;self.scanning=False
            return
        if not identity or phase=='SUSPENDED':return
        if identity!=self.identity or phase=='ACQUIRING':
            self.identity=identity;self.stage=-1;self.completed=False;self.scanning=False
        if phase=='ASSEMBLING':
            self.scanning=True
            stage=sum(fraction>=threshold for threshold in (.2,.5,.8))
            if stage>self.stage:
                self.stage=stage
                if stage:self.play('scan',now)
        elif phase in ('BLINKING','RETURNING'):
            self.scanning=True
        elif phase in ('GREETING','READY') and self.scanning and not self.completed:
            self.completed=True
            self.play('verified',now)
