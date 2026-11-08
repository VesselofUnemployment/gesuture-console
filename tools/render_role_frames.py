"""Abstract role-frame comparison; never uses a camera image."""
import _bootstrap  # Resolve application imports from any working directory.
from project_paths import PROJECT_ROOT
from pathlib import Path
import cv2
import numpy as np
from vision_hud import face_frame,BLUE,demo_frame,draw_hud


def main():
    output=PROJECT_ROOT/'previews'
    output.mkdir(exist_ok=True)
    tiles=[]
    for role in ('USER','OPERATOR','SUPERVISOR','ADMIN'):
        canvas=np.full((400,480,3),(24,21,17),np.uint8)
        cv2.putText(canvas,role,(90,40),cv2.FONT_HERSHEY_SIMPLEX,.6,(200,190,175),1,cv2.LINE_AA)
        cv2.ellipse(canvas,(210,210),(74,100),0,0,360,(70,65,59),2,cv2.LINE_AA)
        face_frame(canvas,90,80,330,320,BLUE,role)
        tiles.append(canvas)
    sheet=np.concatenate((np.concatenate(tiles[:2],axis=1),np.concatenate(tiles[2:],axis=1)),axis=0)
    cv2.imwrite(str(output/'role-frames-preview.png'),sheet)
    frame,face=demo_frame()
    cv2.imwrite(str(output/'frame-design-preview.png'),draw_hud(frame,[(face,'Admin',BLUE,.8)]))
    print('Abstract role-frame previews generated.')


if __name__=='__main__':main()
