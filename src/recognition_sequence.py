"""Measured face mesh + presentation animation; never authorizes a session."""
import time
import cv2
import numpy as np
from bitmap_font import text_mask
from face_tracking import NODES
from vision_hud import BLUE, face_frame, square_bounds, square_side, draw_hud, profile_label, paint_mask

INITIAL = .16
BUILD = 2.0
BLINK = .48
RETURN = .55
GREETING = 2.4
LOSS_GRACE = 5.0
PROFILE_LETTER_INTERVAL = .28
PROFILE_ROLE_DELAY = .55
PROFILE_FIELD_DELAY = .75


def ease(value):
    value = float(np.clip(value, 0, 1))
    return value*value*(3-2*value)


def blend(canvas, layer, alpha):
    alpha = float(np.clip(alpha, 0, 1))
    if alpha > 0:
        cv2.addWeighted(layer, alpha, canvas, 1-alpha, 0, canvas)


def triangulate(points):
    """Freeze triangle connectivity at acquisition, not the moving coordinates."""
    points = np.asarray(points, dtype=np.float32)
    low = np.floor(points.min(axis=0))-4
    shifted = points-low
    size = np.ceil(shifted.max(axis=0)+5).astype(int)
    subdiv = cv2.Subdiv2D((0, 0, int(size[0]), int(size[1])))
    try:
        for point in shifted:
            subdiv.insert(tuple(map(float, point)))
        edges = set()
        for triangle in subdiv.getTriangleList().reshape(-1, 3, 2):
            distances = np.linalg.norm(triangle[:, None, :]-shifted[None, :, :], axis=2)
            indices = distances.argmin(axis=1)
            if np.max(distances.min(axis=1)) > 1 or len(set(indices)) != 3:
                continue
            for a, b in ((indices[0], indices[1]), (indices[1], indices[2]), (indices[2], indices[0])):
                edges.add(tuple(sorted((int(a), int(b)))))
        return sorted(edges)
    except cv2.error:
        return []


class RecognitionSequence:
    def __init__(self, rng=None):
        self.rng = rng if rng is not None else np.random.default_rng()
        self.reset()

    def reset(self):
        self.signature = None
        self.started = self.mesh_started = self.last_time = None
        self.center = self.shape = None
        self.edges = []
        self.phase = 'IDLE'
        self.visible = False
        self.paused_at = self.mesh_lost_at = None

    def pause(self, now):
        if self.paused_at is None:
            self.paused_at = now

    def resume(self, now):
        if self.paused_at is not None:
            elapsed = max(0., now-self.paused_at)
            self.started += elapsed
            if self.mesh_started is not None:
                self.mesh_started += elapsed
            self.paused_at = None

    def suspend(self, now, profiles):
        # This retains presentation progress only, never an identity decision.
        self.visible = False
        if self.signature is not None:
            profile = profiles.get(self.signature[0])
            current = None if profile is None else (profile.identity if hasattr(profile, 'identity') else self.signature[0],
                        profile.name, profile.role, tuple(profile.fields))
            if (profile is None or not profile.active or current != self.signature or
                    now-self.last_time >= LOSS_GRACE):
                self.reset()
                return False
            self.pause(now)
            self.phase = 'SUSPENDED'
        return False

    def prepare(self, results, profiles, frame_shape, now=None):
        now = time.monotonic() if now is None else now
        if len(results) > 1:
            self.reset()
            return False
        if not results or results[0][1] not in profiles:
            return self.suspend(now, profiles)
        if not profiles[results[0][1]].active:
            self.reset()
            return False
        face, label, _, _ = results[0]
        raw = np.asarray(face[:4], float)
        if not np.isfinite(raw).all() or np.any(raw[2:] <= 0):
            return self.suspend(now, profiles)
        profile = profiles[label]
        signature = (label, profile.name, profile.role, tuple(profile.fields))
        shape = tuple(frame_shape[:2])
        center = raw[:2]+raw[2:]/2
        continuous = (signature == self.signature and shape == self.shape and
                      self.last_time is not None and 0 <= now-self.last_time < LOSS_GRACE)
        if not continuous:
            self.reset()
            self.signature, self.started, self.shape = signature, now, shape
        elif not self.visible:
            self.resume(now)
            self.mesh_lost_at = None
        self.visible = True
        self.last_time, self.center = now, center
        self.phase = self.state(now)['phase']
        return self.needs_mesh(now)

    def needs_mesh(self, now):
        effective = self.paused_at if self.paused_at is not None else now
        return self.visible and self.signature is not None and (self.mesh_started is None or
            effective-self.mesh_started < BUILD+BLINK+RETURN+.6)

    def state(self, now):
        now = self.paused_at if self.paused_at is not None else now
        if self.signature is None:
            return dict(phase='IDLE', square=1., expansion=0., mesh=0., fraction=0., greeting=0., profile=None)
        if self.mesh_started is None:
            return dict(phase='ACQUIRING', square=1., expansion=0., mesh=0., fraction=0., greeting=0., profile=None)
        elapsed = max(0., now-self.mesh_started)
        if elapsed < BUILD:
            return dict(phase='ASSEMBLING', square=0., expansion=0., mesh=1., fraction=elapsed/BUILD, greeting=0., profile=None)
        if elapsed < BUILD+BLINK:
            blink = elapsed-BUILD
            return dict(phase='BLINKING', square=0., expansion=0., mesh=1. if int(blink/.08)%2 == 0 else .12,
                        fraction=1., greeting=0., profile=None)
        if elapsed < BUILD+BLINK+RETURN:
            p = ease((elapsed-BUILD-BLINK)/RETURN)
            return dict(phase='RETURNING', square=p, expansion=(1-p)*.18, mesh=1., fraction=1., greeting=0., profile=None)
        g = elapsed-BUILD-BLINK-RETURN
        greeting = ease(g/.45)*ease((GREETING-g)/.5)
        length = len(self.signature[1])
        profile_stage = min(length, 1+int(g/PROFILE_LETTER_INTERVAL))
        name_done = max(0, length-1)*PROFILE_LETTER_INTERVAL
        if g >= name_done+PROFILE_ROLE_DELAY:
            profile_stage = length+1
        if g >= name_done+PROFILE_ROLE_DELAY+PROFILE_FIELD_DELAY:
            profile_stage = length+2
        return dict(phase='GREETING' if g < GREETING else 'READY', square=1., expansion=0.,
                    mesh=1-ease(g/.6), fraction=1., greeting=greeting,
                    profile=profile_stage)

    def render(self, frame, results, profiles, points=None, now=None, tracking_error=''):
        now = time.monotonic() if now is None else now
        if self.signature is None or not self.visible:
            return draw_hud(frame, results, profiles=profiles)
        if points is not None:
            self.resume(now)
            self.mesh_lost_at = None
        if points is not None and now-self.started >= INITIAL and self.mesh_started is None:
            selected = points[list(NODES)]
            edges = triangulate(selected)
            if edges:
                self.edges = [edges[i] for i in self.rng.permutation(len(edges))]
                self.mesh_started = now
        state = self.state(now)
        self.phase = state['phase']
        face, label, _, _ = results[0]
        profile = profiles[label]
        bounds = square_bounds(face, frame.shape)
        canvas = frame.copy()
        # A missing current measurement never produces a stale/fabricated mesh.
        if state['mesh'] > 0 and points is None:
            # Brief tracking failures pause the scan without reusing old points.
            if state['phase'] in ('ASSEMBLING', 'BLINKING', 'RETURNING'):
                self.pause(now)
                if self.mesh_lost_at is None:
                    self.mesh_lost_at = now
                if now-self.mesh_lost_at >= LOSS_GRACE:
                    self.mesh_started = None
                    self.edges = []
                    self.started = now
                    self.paused_at = self.mesh_lost_at = None
            return draw_hud(frame, results, profiles=profiles)
        if points is not None and state['mesh'] > 0:
            layer = canvas.copy()
            selected = np.rint(points[list(NODES)]).astype(int)
            count = min(len(self.edges), int(state['fraction']*len(self.edges)))
            vertices = set()
            for a, b in self.edges[:count]:
                cv2.line(layer, tuple(selected[a]), tuple(selected[b]), (250, 195, 105), 1, cv2.LINE_AA)
                vertices.update((a, b))
            for index in vertices:
                cv2.circle(layer, tuple(selected[index]), 2, (255, 229, 182), -1, cv2.LINE_AA)
            blend(canvas, layer, state['mesh']*.78)
        if state['square'] > 0:
            layer = canvas.copy()
            x1, y1, x2, y2 = bounds
            extra = round((x2-x1)*state['expansion']/2)
            face_frame(layer, x1-extra, y1-extra, x2+extra, y2+extra, BLUE, profile.role)
            blend(canvas, layer, state['square'])
        if state['profile'] is not None:
            layer = canvas.copy()
            profile_label(layer, *bounds, stage=state['profile'], profile=profile)
            g = now-self.mesh_started-BUILD-BLINK-RETURN
            blend(canvas, layer, ease(g/.7))
        elif self.mesh_started is None and now-self.started > .9:
            # Recognition remains usable if tracking fails; do not fake scan success.
            profile_label(canvas, *bounds, profile=profile)
        if state['phase'] in ('ASSEMBLING', 'BLINKING', 'RETURNING'):
            self.caption(canvas, 'VERIFYING...', .7, small=True)
        if state['greeting'] > 0 and profile.role != 'USER':
            self.caption(canvas, 'WELCOME BACK, '+profile.name, state['greeting'],
                         extra='SYSTEM AUTHORITY RESTORED' if profile.role in ('SUPERVISOR', 'ADMIN') else '')
        if tracking_error:
            self.caption(canvas, 'FACE TRACKING UNAVAILABLE', .7, small=True)
        return canvas

    @staticmethod
    def caption(canvas, text, alpha, extra='', small=False):
        h, w = canvas.shape[:2]
        scale = min(h, w)/480
        core = max(7, round((9 if small else 14)*scale))
        glyphs = text_mask(text, core, gap=1, max_width=int(w*.82))
        second = text_mask(extra, max(7, round(8*scale)), max_width=int(w*.8)) if extra else None
        # Fixed lower camera area, separated from the existing side profile.
        total = glyphs.shape[0]+4+(second.shape[0]+12 if second is not None else 0)
        y = max(4, h-round(34*scale)-total+round((1-ease(alpha))*5*scale))
        layer = canvas.copy()
        paint_mask(layer, glyphs, (w-glyphs.shape[1]-4)//2, y)
        if second is not None:
            paint_mask(layer, second, (w-second.shape[1]-4)//2, y+glyphs.shape[0]+12)
        blend(canvas, layer, alpha)
