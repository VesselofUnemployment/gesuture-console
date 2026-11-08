"""Camera-independent tool state. No screenshot, input injection or disk I/O."""
import math
from desktop_control import Rect

WORKSPACE = (('desk-left','PREVIOUS DESKTOP'),('desk-right','NEXT DESKTOP'),
             ('menu','CONTROL WHEEL'),('back','BACK'))
MEDIA = (('media-play','PLAY PAUSE'),('media-next','NEXT TRACK'),
         ('volume-up','VOLUME UP'),('volume-down','VOLUME DOWN'),
         ('media-prev','PREVIOUS TRACK'),('volume-mute','MUTE UNMUTE'),
         ('menu','CONTROL WHEEL'),('back','BACK'))
DRAW_MENU = (('draw-resume','RESUME DRAWING'),('draw-undo','UNDO STROKE'),
             ('draw-clear','CLEAR DRAWING'),('menu','CONTROL WHEEL'),('back','BACK'))
MENUS = {'workspace':WORKSPACE,'media':MEDIA,'draw-menu':DRAW_MENU}
TITLES = {'wheel':'CONTROL','workspace':'WORKSPACE','media':'MEDIA','draw-menu':'AIR DRAW'}


class ToolState:
    MAX_POINTS=8192
    MAX_STROKES=128

    def __init__(self):
        self.hud=False;self.strokes=[];self.stroke=None;self.ink_revision=0
        self.anchor=self.region=self.capture_request=None
        self.confirm=False;self.palm_since=None

    def cancel(self):
        # A lost measurement never completes a capture or joins two strokes.
        self.stroke=None;self.anchor=self.region=None;self.confirm=False
        self.capture_request=None;self.palm_since=None

    def clear(self):
        self.strokes=[];self.stroke=None;self.ink_revision+=1

    def undo(self):
        if self.strokes:self.strokes.pop();self.ink_revision+=1
        self.stroke=None

    def enter(self, action, current):
        if action in ('workspace','media','draw','capture'):
            self.cancel();return action
        if action=='hud':self.hud=not self.hud;return 'ready'
        if action=='menu':self.cancel();return 'wheel'
        if action=='back':self.cancel();return 'ready'
        if action=='draw-clear':self.clear();return 'draw'
        if action=='draw-undo':self.undo();return 'draw'
        if action=='draw-resume':return 'draw'
        return current

    def update(self, mode, point, edge, down, hand, now, scale):
        """Return (mode, message, action); pinch must already be fresh/armed."""
        if mode=='draw':
            if hand['open']:
                self.stroke=None
                if self.palm_since is None:self.palm_since=now
                if now-self.palm_since>=.4:
                    self.palm_since=None
                    return 'draw-menu','POINT / PINCH TO SELECT',None
            else:self.palm_since=None
            if edge=='down' and not hand['open']:
                if sum(map(len,self.strokes))>=self.MAX_POINTS:
                    return mode,'INK LIMIT / OPEN PALM TO CLEAR',None
                if len(self.strokes)>=self.MAX_STROKES:self.strokes.pop(0)
                self.stroke=[tuple(point)];self.strokes.append(self.stroke);self.ink_revision+=1
            elif edge=='up':self.stroke=None
            elif down and self.stroke is not None:
                if math.dist(self.stroke[-1],point)>=2*scale:
                    if sum(map(len,self.strokes))>=self.MAX_POINTS:
                        # End this stroke instead of growing memory forever.
                        self.stroke=None
                        return mode,'INK LIMIT / OPEN PALM TO CLEAR',None
                    self.stroke.append(tuple(point));self.ink_revision+=1
            return mode,'PINCH TO DRAW / OPEN PALM FOR TOOLS / FIST TO BACK',None
        if mode=='capture':
            if hand['open']:
                self.anchor=self.region=None;self.confirm=False
            elif edge=='down':
                if self.confirm and self.region is not None:
                    self.capture_request=self.region
                    self.anchor=self.region=None;self.confirm=False
                    return mode,'SAVING CAPTURE...', 'capture-save'
                self.anchor=tuple(point);self.confirm=False
            if self.anchor is not None:
                x,y=self.anchor;px,py=point
                self.region=Rect(round(min(x,px)),round(min(y,py)),
                                 round(max(x,px)),round(max(y,py)))
                if edge=='up':
                    self.anchor=None
                    self.confirm=self.region.width>=24*scale and self.region.height>=24*scale
                    if not self.confirm:self.region=None
            if self.confirm:
                message='PINCH AGAIN TO SAVE / OPEN PALM TO CANCEL'
            elif self.anchor is not None:message='SELECTING REGION / RELEASE TO PREVIEW'
            else:message='PINCH + DRAG A REGION / FIST TO BACK'
            return mode,message,None
        return mode,'',None
