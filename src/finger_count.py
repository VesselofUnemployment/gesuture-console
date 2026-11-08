"""Number poses from 3D joints, with a palm-facing 2D check for folded thumbs."""
import numpy as np


def angle(a,b,c):
    left,right=a-b,c-b
    scale=float(np.linalg.norm(left)*np.linalg.norm(right))
    if scale<1e-10:return 0.
    return float(np.degrees(np.arccos(np.clip(np.dot(left,right)/scale,-1,1))))


def projected_number(points):
    """Resolve 3/4 when depth is uncertain, never just count visible tips.

    A palm facing the camera, individually straight/folded fingers, and both
    thumb joints tucked over the palm are required. Side views stay ambiguous.
    Coordinates are isotropic image pixels, not stretched unit-square axes.
    """
    p=np.asarray(points,dtype=float)
    if p.shape!=(21,2) or not np.isfinite(p).all():return None
    width=float(np.linalg.norm(p[5]-p[17]))
    length=float(np.linalg.norm(np.mean(p[[5,9,13,17]],axis=0)-p[0]))
    if min(width,length)<1e-6 or width/length<.35:return None
    palm=p[[0,5,9,13,17]]
    x,y=palm[:,0],palm[:,1]
    area=abs(float(np.dot(x,np.roll(y,1))-np.dot(y,np.roll(x,1))))/2
    if area/(width*length)<.25:return None
    def within_palm(point):
        distances=[]
        for a,b in zip(palm,np.roll(palm,-1,axis=0)):
            edge=b-a;normal=float(np.linalg.norm(edge))
            if normal<1e-6:continue
            delta=point-a
            distances.append(float(edge[0]*delta[1]-edge[1]*delta[0])/normal)
        margin=.10*width
        return bool(distances) and (min(distances)>=-margin or max(distances)<=margin)
    # An outstretched thumb can be straight and close to the index base.
    # Its tip AND last joint must lie over the palm for this special fallback.
    if not within_palm(p[3]) or not within_palm(p[4]):return None
    flags=[]
    for mcp,pip,dip,tip in ((5,6,7,8),(9,10,11,12),(13,14,15,16),(17,18,19,20)):
        bend,end=angle(p[mcp],p[pip],p[dip]),angle(p[pip],p[dip],p[tip])
        ratio=float(np.linalg.norm(p[tip]-p[0])/max(1e-8,np.linalg.norm(p[pip]-p[0])))
        if bend>=150 and end>=140 and ratio>=1.08:flags.append(True)
        elif bend<130 or end<115 or ratio<.96:flags.append(False)
        else:return None
    return {(True,True,True,False):3,(True,True,True,True):4}.get(tuple(flags))


def number_pose(points,gesture='',image_points=None):
    points=np.asarray(points,dtype=float)
    if points.shape!=(21,3) or not np.isfinite(points).all():return None
    if np.linalg.norm(points[0]-points[9])<1e-6:return None
    if gesture in ('ILoveYou','Thumb_Up','Thumb_Down'):return None
    fallback={'Pointing_Up':1,'Victory':2,'Open_Palm':5}.get(gesture)
    # Folded thumbs often have unreliable world depth; their two projected
    # joints resting on the palm give independent evidence for 3/4.
    def recover():
        return projected_number(image_points) if image_points is not None else None
    flags=[]
    for mcp,pip,dip,tip in ((5,6,7,8),(9,10,11,12),(13,14,15,16),(17,18,19,20)):
        bend=angle(points[mcp],points[pip],points[dip])
        end=angle(points[pip],points[dip],points[tip])
        ratio=float(np.linalg.norm(points[tip]-points[0])/max(1e-8,np.linalg.norm(points[pip]-points[0])))
        if bend>=155 and end>=145 and ratio>=1.08:flags.append(True)
        elif bend<135 or end<120 or ratio<.98:flags.append(False)
        else:
            projected=recover()
            return projected if projected is not None else fallback
    width=float(np.linalg.norm(points[5]-points[17]))
    if width<1e-6:return None
    separation=float(np.linalg.norm(points[4]-points[5])/width)
    base,end=angle(points[1],points[2],points[3]),angle(points[2],points[3],points[4])
    if base>=145 and end>=150 and separation>=.7:thumb=True
    elif base<130 or end<125 or separation<.5:thumb=False
    else:
        projected=recover()
        return projected if projected is not None else fallback
    pose=(thumb,*flags)
    canonical={(False,True,False,False,False):1,(False,True,True,False,False):2,
               (False,True,True,True,False):3,(True,True,True,False,False):3,
               (False,True,True,True,True):4,(True,True,True,True,True):5,
               (False,False,False,False,False):0}
    counted=canonical.get(pose)
    # Preserve the established 0/1/2 and thumb-index-middle 3 poses. Strong
    # projected evidence can resolve a tucked thumb mistaken for an open one.
    if counted in (None,5):
        projected=recover()
        if projected is not None:return projected
    return counted if counted is not None else (fallback if gesture=='Pointing_Up' else None)
