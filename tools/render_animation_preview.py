"""Visual QA on an abstract face; never saves a camera/photo frame."""
import _bootstrap  # Resolve application imports from any working directory.
from project_paths import PROJECT_ROOT
import argparse
import importlib
from pathlib import Path
from types import SimpleNamespace
import cv2
import numpy as np
from PIL import Image
from face_tracking import FaceTracker, NODES
from recognition_sequence import RecognitionSequence
from vision_hud import demo_frame


def main():
    parser = argparse.ArgumentParser(description='Render a synthetic preview using reference landmark geometry.')
    parser.add_argument('reference_image', type=Path)
    args = parser.parse_args()
    image = cv2.imread(str(args.reference_image))
    if image is None:
        parser.error('The reference image could not be read.')
    detector,_=importlib.import_module('face_enrollment').load_models()
    detector.setInputSize((image.shape[1],image.shape[0]));_,faces=detector.detect(image)
    if faces is None or len(faces) != 1:
        parser.error('The reference image must contain exactly one face.')
    tracker=FaceTracker()
    try:points=tracker.measure(image,faces[0],0)
    finally:tracker.close()
    low,high=points.min(axis=0),points.max(axis=0)
    points=(points-low)/(high-low)*np.array([164,226])+np.array([198,126])
    points=points.astype(np.float32)
    frame,face=demo_frame()
    profiles={'demo':SimpleNamespace(name='admin',role='ADMIN',fields=('Software','Mechanics'),active=True)}
    results=[(face,'demo',None,.85)]
    sequence=RecognitionSequence(np.random.default_rng(8))
    images=[];snapshots=[]
    for index,now in enumerate(np.arange(0,7.6,.05)):
        sequence.prepare(results,profiles,frame.shape,float(now))
        output=sequence.render(frame,results,profiles,points,float(now))
        images.append(Image.fromarray(cv2.cvtColor(output,cv2.COLOR_BGR2RGB)))
        if index in (0,24,46,60,73,104,140):
            cv2.putText(output,sequence.phase,(14,25),cv2.FONT_HERSHEY_SIMPLEX,.5,(165,165,165),1,cv2.LINE_AA)
            snapshots.append(output)
    destination=PROJECT_ROOT/'previews'
    destination.mkdir(exist_ok=True)
    images[0].save(destination/'face-animation-preview.gif',save_all=True,append_images=images[1:],duration=50,loop=0)
    while len(snapshots)%3:snapshots.append(np.zeros_like(frame))
    sheet=np.concatenate([np.concatenate(snapshots[i:i+3],axis=1) for i in range(0,len(snapshots),3)],axis=0)
    cv2.imwrite(str(destination/'face-animation-stages.png'),sheet)
    print('Abstract preview and animation stage sheet generated.')


if __name__=='__main__':main()
