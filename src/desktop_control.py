"""Hand mouse, command wheel and window drag policy. No held OS buttons.

All points are from the existing mirrored MediaPipe landmarks. This module can
be tested without a camera, desktop windows, or application launches.
"""
import math
import time
from dataclasses import dataclass

COMMANDS = (('workspace','WORKSPACE SHIFT'), ('media','MEDIA CONTROL'),
            ('capture','REGION CAPTURE'), ('hud','SYSTEM HUD'),
            ('draw','AIR DRAW'), ('move','WINDOW CONTROL'),
            ('back','BACK'), ('off','EXIT CONTROL'))


@dataclass(frozen=True)
class Rect:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self): return self.right-self.left
    @property
    def height(self): return self.bottom-self.top


def hand_input(hand, shape):
    """Scale-independent pinch ratio; pixel landmarks never leave local memory."""
    points = getattr(hand,'raw_points',None)
    if points is None:points=hand.points
    height, width = shape[:2]
    if len(points) != 21 or width < 2 or height < 2:return None
    def distance(a, b): return math.hypot(*(points[a]-points[b]))
    palm = max(distance(5,17), distance(0,9)*.75)
    x,y = map(float,points[8])
    # Palm centres do not move when the fingers close. Use camera-width units
    # on BOTH axes, without the cursor's acceleration or edge clipping.
    anchor=tuple(sum(float(points[i][axis]) for i in (0,5,9,13,17))/5/(width-1)
                 for axis in (0,1))
    ratio = distance(4,8)/palm if palm >= 12 else float('nan')
    if not all(math.isfinite(v) for v in (x,y,ratio,*anchor)):return None
    return dict(number=hand.number, position=(x/(width-1),y/(height-1)),
                resize_position=anchor, pinch=ratio,
                open=hand.gesture=='Open_Palm', fist=hand.gesture=='Closed_Fist')


def proportional_size(initial, factor, minimum, maximum):
    """Clamp one scale, never width and height independently."""
    lower=max(minimum[0]/initial.width,minimum[1]/initial.height)
    upper=min(maximum[0]/initial.width,maximum[1]/initial.height)
    if lower>upper:return None
    factor=min(upper,max(lower,factor))
    return round(initial.width*factor),round(initial.height*factor)


def screen_point(position, area):
    # Comfortable central camera range reaches the whole desktop.
    x,y=position
    x=max(0.,min(1.,(x-.16)/.68));y=max(0.,min(1.,(y-.14)/.72))
    return (area.left+x*(area.width-1),area.top+y*(area.height-1))


def sector(point, center, radius, count=8):
    dx,dy=point[0]-center[0],point[1]-center[1]
    if not radius*.36 <= math.hypot(dx,dy) <= radius*1.6:return None
    step=math.tau/count
    return int(((math.atan2(dy,dx)+math.pi/2+step/2)%math.tau)/step)%count


def steady_sector(point, center, radius, previous):
    """Highlight the slice actually under the visible crosshair."""
    return sector(point,center,radius)


def filtered_point(previous, raw, elapsed, scale=1.):
    if previous is None:return raw
    distance=math.dist(previous,raw)/scale
    if distance<=3.:return previous
    # Smooth a resting hand; large intentional movements catch up in one frame.
    speed=max(0.,min(1.,(distance-10.)/50.))
    tau=.050*(1-speed)+.006*speed
    alpha=1-math.exp(-max(.001,elapsed)/tau)
    return tuple(a+alpha*(b-a) for a,b in zip(previous,raw))


def snap_rect(point, area, scale=1.):
    edge=max(24,round(32*scale))
    middle=area.left+area.width//2
    if point[0] <= area.left+edge:return Rect(area.left,area.top,middle,area.bottom)
    if point[0] >= area.right-edge:return Rect(middle,area.top,area.right,area.bottom)
    return None


class Pinch:
    def __init__(self):self.reset()
    def reset(self):
        self.down=False;self.candidate=None;self.since=0.;self.armed=False
    def update(self, ratio, now):
        target = ratio < .30 if not self.down else ratio < .48
        if target != self.candidate:self.candidate,self.since=target,now
        if now-self.since < .045:return None
        if not target:self.armed=True
        if target == self.down:return None
        self.down=target
        if not target:return 'up'
        return 'down' if self.armed else None


class ControlPolicy:
    LOSS_GRACE = 3.
    RESIZE_GAIN = 1.5
    RESIZE_DEADZONE = .006

    def __init__(self, backend):
        self.backend=backend
        self.enabled=False
        self.reset()

    def reset(self):
        from desktop_tools import ToolState
        self.tools=ToolState()
        self.mode='ready';self.pinch=Pinch();self.hand=None
        self.point=None;self.open_since=None;self.hover=None;self.hover_since=None
        self.target=None;self.drag=None;self.offset=None;self.preview=None
        self.block_open=False;self.last=None;self.message='OPEN PALM / COMMAND WHEEL'
        self.tracking=False;self.missing_since=None
        self.pinches={};self.points={};self.resize=None;self.bounds=None;self.fist_since=None
        self.resize_points={}
        self.mouse_anchor=None

    def set_enabled(self, value):
        self.enabled=bool(value);self.reset()

    def suspend(self):
        self.reset()

    def cancel_hold(self):
        # No synthetic release/snap on a missing or ambiguous measurement.
        self.drag=self.target=self.preview=self.offset=self.bounds=self.resize=None
        self.pinches={};self.points={};self.pinch=Pinch();self.hand=None
        self.resize_points={}
        self.mouse_anchor=None
        self.fist_since=self.open_since=None;self.hover_since=None
        self.tools.cancel()

    def missing(self, now):
        self.cancel_hold()
        self.tracking=False
        if self.missing_since is None:self.missing_since=now
        if self.last is None or now-self.last>=self.LOSS_GRACE:self.suspend()

    def expire(self, now):
        if self.last is not None and now-self.last>.22:self.missing(now)

    def resize_rectangle(self, distance):
        initial,span,minimum,maximum=self.resize
        area=self.backend.area
        delta=distance-span
        delta=math.copysign(max(0.,abs(delta)-self.RESIZE_DEADZONE),delta)
        # Fixed camera-space gain: close hands are no longer an amplifier.
        factor=math.exp(max(-4.,min(4.,self.RESIZE_GAIN*delta)))
        width,height=proportional_size(initial,factor,minimum,maximum)
        cx=(initial.left+initial.right)/2;cy=(initial.top+initial.bottom)/2
        left=max(area.left,min(area.right-width,round(cx-width/2)))
        top=max(area.top,min(area.bottom-height,round(cy-height/2)))
        return Rect(left,top,left+width,top+height)

    def update(self, hands, now, authorized=True):
        if not self.enabled:return None
        if not authorized:self.suspend();return None
        if not hands or any(h is None for h in hands) or len(hands)>2:
            self.missing(now);return None
        if self.last is not None and (now<=self.last or now-self.last>.22):
            if now<=self.last:return None
            self.missing(now)
        if self.mode!='move' and len(hands)>1:
            self.missing(now);return None
        numbers={h['number'] for h in hands}
        if len(numbers)!=len(hands):self.missing(now);return None
        # Reassigned/lost hands must release before they can grab another window.
        if self.hand is not None and self.hand not in numbers:
            self.cancel_hold()
        if self.resize is not None and numbers!=set(self.pinches):self.cancel_hold()
        if len(hands)==1 and len(self.pinches)==2:self.cancel_hold()
        previous_point=self.point if self.tracking and self.hand is not None else None
        elapsed=0 if self.last is None else now-self.last
        self.last=now;self.tracking=True;self.missing_since=None
        for number in set(self.pinches)-numbers:
            self.pinches.pop(number,None);self.points.pop(number,None)
            self.resize_points.pop(number,None)
        edges={}
        for hand in hands:
            number=hand['number']
            area=getattr(self.backend,'mouse_area',self.backend.area) if self.mode=='ready' else self.backend.area
            raw=screen_point(hand['position'],area)
            self.points[number]=filtered_point(self.points.get(number),raw,elapsed,self.backend.scale)
            anchor=hand.get('resize_position',hand['position'])
            self.resize_points[number]=filtered_point(self.resize_points.get(number),anchor,elapsed,1/1200)
            pinch=self.pinches.setdefault(number,Pinch())
            edges[number]=pinch.update(hand['pinch'],now)
        if self.hand is None:self.hand=hands[0]['number']
        hand=next(h for h in hands if h['number']==self.hand)
        self.pinch=self.pinches[self.hand];edge=edges[self.hand]
        self.point=self.points[self.hand]
        area=self.backend.area

        if self.mode!='ready':
            if any(h.get('fist',False) for h in hands):
                # Stop moving immediately; only a deliberate hold exits the mode.
                self.drag=self.target=self.preview=self.resize=self.bounds=None
                self.tools.cancel()
                if self.fist_since is None:self.fist_since=now
                self.message='HOLD FIST / BACK TO MOUSE'
                if now-self.fist_since>=.5:
                    tools=self.tools
                    self.suspend();self.tools=tools;self.block_open=True
                return None
            self.fist_since=None
        # Palm only opens the wheel from ready. It cannot leave window control.
        if not hand['open']:self.block_open=False;self.open_since=None
        elif self.mode=='ready' and not self.block_open:
            if self.open_since is None:self.open_since=now
            if now-self.open_since>=.4:
                self.mode='wheel';self.block_open=True
        self.preview=None
        if self.mode=='ready':
            self.message='MOUSE / PINCH TO CLICK / OPEN PALM FOR WHEEL'
            # Hold the last visible aim while fingers close; this affects only
            # mouse clicks, never wheel selection or window manipulation.
            clicking=not hand['open'] and not hand.get('fist',False)
            if clicking and self.pinch.armed and (self.pinch.candidate or self.pinch.down):
                if self.mouse_anchor is None:self.mouse_anchor=previous_point or self.point
                self.point=self.mouse_anchor
            else:self.mouse_anchor=None
            if edge=='down' and clicking:return 'mouse-click'
        else:self.mouse_anchor=None
        from desktop_tools import MENUS
        commands=COMMANDS if self.mode=='wheel' else MENUS.get(self.mode)
        if commands:
            selected=sector(self.point,self.backend.center,self.backend.radius,len(commands))
            if selected!=self.hover or self.hover_since is None:self.hover,self.hover_since=selected,now
            self.message=commands[selected][1] if selected is not None else 'POINT / PINCH TO SELECT'
            # Highlight and commit use the exact same point; no invisible aim lock.
            if edge=='down' and selected is not None and now-self.hover_since>=.045:
                action=commands[selected][0]
                self.mode='move' if action=='move' else self.tools.enter(action,self.mode)
                self.hover=None;self.hover_since=None;self.open_since=None;self.block_open=True
                self.pinch.armed=False
                self.message='POINT AT WINDOW / PINCH TO GRAB' if action=='move' else 'OPEN PALM / COMMAND WHEEL'
                return action
        elif self.mode in ('draw','capture'):
            self.mode,self.message,action=self.tools.update(self.mode,self.point,edge,
                self.pinch.down and self.pinch.armed,hand,now,self.backend.scale)
            if self.mode=='draw-menu':
                self.hover=None;self.hover_since=None;self.pinch.armed=False
            return action
        elif self.mode=='move':
            if self.resize is not None:
                if not self.backend.valid(self.drag):
                    self.cancel_hold();self.message='WINDOW UNAVAILABLE';return None
                if not all(p.down for p in self.pinches.values()):
                    self.cancel_hold();self.message='SIZE SET / RELEASE BOTH HANDS';return None
                span=math.dist(*self.resize_points.values())
                rectangle=self.resize_rectangle(span)
                if rectangle!=self.bounds:
                    if not self.backend.place(self.drag,rectangle):
                        self.cancel_hold();self.message='WINDOW CANNOT BE RESIZED';return None
                    self.bounds=rectangle
                self.target=rectangle;self.message='RESIZING / RELEASE TO SET / FIST TO EXIT'
                return None
            if len(hands)==2:
                # Two hands suppress one-hand motion until the resize is armed.
                if all(p.down and p.armed for p in self.pinches.values()):
                    span=math.dist(*self.resize_points.values())
                    if span<.08:
                        self.message='SPREAD HANDS / PINCH TO RESIZE';return None
                    center=tuple(sum(p[i] for p in self.points.values())/2 for i in (0,1))
                    candidate=self.drag or self.backend.pick(center)
                    if candidate is not None and candidate.resizable:
                        target=self.backend.grab(candidate,center) if self.drag is None else self.drag
                        if target is not None:
                            self.drag=target;self.bounds=self.bounds or target.rect
                            # Read app-specific limits once per gesture, off the
                            # per-frame path. A failed query allows growth only.
                            minimum,maximum=self.backend.resize_limits(target)
                            if proportional_size(self.bounds,1.,minimum,maximum) is None:
                                self.cancel_hold();self.message='WINDOW SIZE LIMITS';return None
                            self.resize=(self.bounds,span,minimum,maximum);self.target=self.bounds
                            self.message='RESIZING / RELEASE TO SET / FIST TO EXIT'
                    elif candidate is not None:self.message='WINDOW HAS FIXED SIZE'
                else:self.message='RELEASE BOTH PINCHES / PINCH TO RESIZE'
                return None
            if self.drag is not None:
                if not self.backend.valid(self.drag):
                    self.cancel_hold();self.message='WINDOW UNAVAILABLE'
                elif edge=='up':
                    destination=snap_rect(self.point,area,self.backend.scale) if self.drag.resizable else None
                    if destination is not None:self.backend.place(self.drag,destination)
                    self.drag=None;self.target=None;self.bounds=None;self.offset=None
                    self.message='RELEASED / PINCH TO GRAB / FIST TO EXIT'
                else:
                    original=self.bounds or self.drag.rect
                    left=round(self.point[0]-self.offset[0]);top=round(self.point[1]-self.offset[1])
                    left=max(area.left-original.width+80,min(area.right-80,left))
                    top=max(area.top,min(area.bottom-40,top))
                    moved=Rect(left,top,left+original.width,top+original.height)
                    if moved!=self.bounds and not self.backend.place(self.drag,moved):
                        self.cancel_hold();self.message='WINDOW CANNOT BE MOVED'
                    else:
                        self.bounds=moved;self.preview=snap_rect(self.point,area,self.backend.scale) if self.drag.resizable else None
                        self.target=moved;self.message='WINDOW HELD / RELEASE TO DROP / FIST TO EXIT'
            else:
                candidate=self.backend.pick(self.point)
                self.target=candidate.rect if candidate else None
                self.message=(candidate.title[:38]+' / PINCH TO GRAB') if candidate else 'POINT AT WINDOW / PINCH TO GRAB'
                if edge=='down' and candidate is not None:
                    self.drag=self.backend.grab(candidate,self.point)
                    if self.drag is not None:
                        self.bounds=self.drag.rect
                        self.offset=(self.point[0]-self.drag.rect.left,self.point[1]-self.drag.rect.top)
                    else:self.message='WINDOW CANNOT BE MOVED'
        return None

    def view(self):
        from desktop_tools import MENUS, TITLES
        return dict(mode=self.mode,point=self.point,hover=self.hover,
                    pinched=self.pinch.down,target=self.target,preview=self.preview,message=self.message,
                    tracking=self.tracking,commands=COMMANDS if self.mode=='wheel' else MENUS.get(self.mode),
                    title=TITLES.get(self.mode,''),region=self.tools.region,
                    ink=self.tools.strokes,ink_revision=self.tools.ink_revision,hud=self.tools.hud)


class DesktopControl:
    """Main UI-thread adapter. Native resources are created only on first use."""
    def __init__(self, launcher, on_state=None, backend_factory=None, overlay_factory=None, services_factory=None):
        self.launcher,self.on_state=launcher,on_state
        self.backend_factory,self.overlay_factory=backend_factory,overlay_factory
        self.backend=self.overlay=self.policy=None
        self.services_factory=services_factory;self.services=None
        self.capture_pending=None;self.toast='';self.toast_until=0.
        self.rebuild_at=None
        self.enabled=False;self.error=''

    def set_enabled(self, value):
        try:
            if not value and self.backend:self.backend.release_mouse()
            if self.services:self.services.deactivate()
            self.capture_pending=None;self.toast=''
            self.rebuild_at=None
            if value and self.policy is None:
                from desktop_windows import WindowBackend
                from control_overlay import ControlOverlay
                self.backend=(self.backend_factory or WindowBackend)()
                self.policy=ControlPolicy(self.backend)
                self.overlay=(self.overlay_factory or ControlOverlay)()
                if self.services_factory:
                    self.services=self.services_factory()
                elif self.backend_factory is None:
                    from desktop_services import ToolServices
                    self.services=ToolServices()
            self.enabled=bool(value)
            if self.policy:self.policy.set_enabled(self.enabled)
            if self.overlay:self.overlay.hide()
            self.error=''
        except (OSError,ValueError) as error:
            self.enabled=False;self.error=str(error)
            if self.overlay:self.overlay.close()
            if self.services:self.services.close();self.services=None
            self.backend=self.policy=self.overlay=None
        if self.on_state:self.on_state(self.enabled)
        return self.enabled

    def update(self, hands, timestamp, authorized, fresh=True):
        if not self.enabled:return
        if not authorized:
            self.set_enabled(False);return
        try:
            if not fresh:
                self.policy.missing(time.monotonic())
                if self.capture_pending is not None:
                    self.capture_pending=None
                    if self.services:self.services.cancel_capture()
                self.present()
                return
            previous_stamp=self.policy.last
            action=self.policy.update(hands,timestamp)
            if action=='off':self.set_enabled(False);return
            if (self.policy.mode=='ready' and self.policy.tracking and self.policy.last!=previous_stamp
                    and action in (None,'mouse-click')):
                if not self.backend.move_cursor(self.policy.point):raise OSError('Mouse control unavailable')
                if action=='mouse-click' and not self.backend.click_left(self.policy.point):
                    raise OSError('Mouse click unavailable')
            if action and action not in dict(COMMANDS) and action!='mouse-click':
                if action=='capture-save' and self.services:
                    self.overlay.hide()
                    self.capture_pending=(time.monotonic()+.09,self.policy.tools.capture_request)
                elif action in ('desk-left','desk-right','media-play','media-next','media-prev',
                                'volume-up','volume-down','volume-mute'):
                    ok=self.backend.command(action)
                    self.toast=action.upper().replace('-',' ') if ok else 'INPUT BUSY / RELEASE PHYSICAL KEYS'
                    self.toast_until=time.monotonic()+1.2
                    if ok and action.startswith('desk-'):
                        # Tool windows belong to a virtual desktop. Recreate
                        # after Windows switches so the HUD follows the user.
                        self.overlay.hide();self.rebuild_at=time.monotonic()+.65
            self.present()
        except (OSError,ValueError) as error:
            self.set_enabled(False);self.error=str(error)

    def poll(self, now):
        # Retry only an incomplete injected release, even after disabling. No
        # button is intentionally held across frames, a lost hand, or shutdown.
        if self.backend:self.backend.release_mouse()
        if not self.enabled:return
        if self.backend.escape_pressed():self.set_enabled(False)
        else:
            self.policy.expire(now)
            if self.rebuild_at is not None and now>=self.rebuild_at:
                try:
                    self.overlay.close()
                    from control_overlay import ControlOverlay
                    self.overlay=(self.overlay_factory or ControlOverlay)()
                    self.rebuild_at=None
                except (OSError,ValueError) as error:
                    self.set_enabled(False);self.error=str(error);return
            if self.capture_pending is not None:
                due,rectangle=self.capture_pending
                if not self.policy.tracking or self.policy.mode!='capture':
                    self.capture_pending=None
                    if self.services:self.services.cancel_capture()
                elif now>=due:
                    self.capture_pending=None
                    if self.services:
                        ok=self.services.capture(rectangle)
                        self.toast='SAVING CAPTURE...' if ok else 'CAPTURE BUSY'
                        self.toast_until=now+2.
            self.present()

    def present(self):
        if self.services:
            self.services.set_hud(self.policy.tools.hud)
            result=self.services.result()
            if result:
                self.toast=result;self.toast_until=time.monotonic()+3.
        if self.capture_pending is not None or self.rebuild_at is not None:
            self.overlay.hide();return
        state=self.policy.view()
        if self.toast and time.monotonic()<self.toast_until:state['message']=self.toast
        if self.services:state['metrics']=self.services.metrics()
        if self.policy.point is None and not state['hud'] and not state['ink']:
            self.overlay.hide()
        else:self.overlay.show(state,self.backend)

    def close(self):
        self.set_enabled(False)
        if self.overlay:self.overlay.close();self.overlay=None
        if self.services:self.services.close();self.services=None
