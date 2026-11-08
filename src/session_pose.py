"""Upward two-finger/thumb pose for SESSION START, using existing landmarks."""
import numpy as np
import cv2
from finger_count import angle


def session_pose(world,pixels):
    p=np.asarray(pixels,dtype=float);w=np.asarray(world,dtype=float)
    if p.shape!=(21,2) or w.shape!=(21,3) or not np.isfinite(p).all() or not np.isfinite(w).all():return False
    palm=max(float(np.linalg.norm(p[0]-p[9])),float(np.linalg.norm(p[5]-p[17])))
    if palm<12:return False
    for m in (5,9):
        vector=p[m+3]-p[m]
        if angle(p[m],p[m+1],p[m+2])<150 or angle(p[m+1],p[m+2],p[m+3])<135:return False
        if -vector[1]<.65*palm or -vector[1]<1.4*abs(vector[0]):return False
    if np.linalg.norm(p[8]-p[12])>.65*palm:return False
    for m in (13,17):
        if not (angle(p[m],p[m+1],p[m+2])<125 or np.linalg.norm(p[m+3]-p[m])<.5*palm):return False
    width=float(np.linalg.norm(w[5]-w[17]))
    world_thumb=(width>1e-6 and angle(w[1],w[2],w[3])>=135
                 and angle(w[2],w[3],w[4])>=140 and np.linalg.norm(w[4]-w[5])/width>=.65)
    axis=(p[8]+p[12]-p[5]-p[9])/2
    thumb=p[4]-p[5]
    lateral=abs(float(axis[0]*thumb[1]-axis[1]*thumb[0]))/max(1e-6,float(np.linalg.norm(axis)))
    image_thumb=(angle(p[2],p[3],p[4])>=145 and lateral>=.30*palm
                 and np.linalg.norm(p[4]-p[5])>=.55*palm)
    # The reference pose has a raised but slightly bent thumb. Its tip and
    # last joint project outside the palm even when 3D calls it folded.
    hull=cv2.convexHull(p[[0,5,9,13,17]].astype(np.float32))
    tip=cv2.pointPolygonTest(hull,tuple(map(float,p[4])),True)
    joint=cv2.pointPolygonTest(hull,tuple(map(float,p[3])),True)
    raised_thumb=tip<-.15*palm and joint<-.05*palm and angle(p[2],p[3],p[4])>=120
    return bool(tip<-.08*palm and (world_thumb or image_thumb or raised_thumb))


def thumb_three(world,pixels=None):
    """Reserve the old thumb/index/middle 3 variant; normal three stays intact."""
    p=np.asarray(world,dtype=float)
    if p.shape!=(21,3) or not np.isfinite(p).all():return False
    # Called only after the existing number classifier returned 3.
    folded=angle(p[13],p[14],p[15])<135 or angle(p[14],p[15],p[16])<120
    if not folded or pixels is None:return folded
    image=np.asarray(pixels,dtype=float)
    palm=max(float(np.linalg.norm(image[0]-image[9])),float(np.linalg.norm(image[5]-image[17])))
    return bool(angle(image[13],image[14],image[15])<130 or
                np.linalg.norm(image[16]-image[13])<.5*palm)
