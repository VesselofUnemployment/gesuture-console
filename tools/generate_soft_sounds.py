"""Generate original, low-level cues without downloaded audio."""
import _bootstrap  # Resolve application imports from any working directory.
from project_paths import PROJECT_ROOT
from pathlib import Path
import wave
import numpy as np


def make_sounds(directory):
    directory=Path(directory)
    directory.mkdir(parents=True,exist_ok=True)
    rate=24000
    cues={'scan':(.055,((0,.045,1180,.014),)),
          'verified':(.28,((0,.14,660,.018),(.095,.165,880,.016))),
          'panel':(.12,((0,.11,520,.016),))}
    for name,(duration,notes) in cues.items():
        samples=np.zeros(round(duration*rate),np.float64)
        for start,length,frequency,gain in notes:
            count=round(length*rate)
            time=np.arange(count)/rate
            # Raised-cosine envelope removes the hard click of square-wave beeps.
            envelope=np.sin(np.pi*np.arange(count)/max(1,count-1))**2
            tone=(np.sin(2*np.pi*frequency*time)+.12*np.sin(4*np.pi*frequency*time))*gain*envelope
            begin=round(start*rate)
            samples[begin:begin+count]+=tone[:len(samples)-begin]
        pcm=np.rint(samples*32767).astype('<i2')
        with wave.open(str(directory/(name+'.wav')),'wb') as output:
            output.setnchannels(1);output.setsampwidth(2);output.setframerate(rate)
            output.writeframes(pcm.tobytes())


if __name__=='__main__':
    make_sounds(PROJECT_ROOT/'assets'/'sounds')
    print('Three quiet original cues generated.')
