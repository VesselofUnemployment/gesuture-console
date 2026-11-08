"""Draw a face frame and identity label over the camera preview."""
from collections import deque
from functools import lru_cache
from pathlib import Path
import math
import time

import cv2
import numpy as np

BLUE = (255, 185, 65)
RED = (75, 75, 235)
AMBER = (80, 175, 230)
WHITE = (245, 245, 245)
DARK = (15, 15, 15)


def stroke(canvas, start, end, color, thickness):
    # A thin dark outline keeps the frame visible on bright backgrounds.
    cv2.line(canvas, start, end, DARK, thickness + 2, cv2.LINE_AA)
    cv2.line(canvas, start, end, color, thickness, cv2.LINE_AA)


SIZE_BANDS = (.22, .28, .34, .42, .50, .60, .72, .86)


def square_levels(frame_shape):
    short_side = min(frame_shape[:2])
    maximum = max(1, short_side-10)
    return tuple(sorted({min(maximum, max(24, round(short_side*fraction)))
                         for fraction in SIZE_BANDS}))


def square_side(frame_shape, face=None):
    if face is None:
        return min(min(frame_shape[:2])-10, max(24, round(min(frame_shape[:2])*.50)))
    # BoxSmoother emits equal width/height: preserve its animated display size.
    # Quantizing these intermediate squares again would turn the tween into jumps.
    if float(face[2]) == float(face[3]):
        return min(min(frame_shape[:2])-10, max(24, round(float(face[3]))))
    # Face height is less affected by left/right head turns than face width.
    target = float(face[3])*.98
    return min(square_levels(frame_shape), key=lambda level: abs(level-target))


class DistanceSizer:
    """Stable size bands with separate thresholds for growing and shrinking."""
    def __init__(self, hold=.08, hysteresis=.035, transition=.10):
        self.hold, self.hysteresis = hold, hysteresis
        self.transition = transition
        self.reset()

    def reset(self):
        self.history = deque(maxlen=3)
        self.side = self.shape = self.last_seen = None
        self.pending = self.pending_since = None
        self.transition_from = self.transition_started = None

    def display_side(self, now):
        if self.transition_started is None or self.transition <= 0:
            return self.side
        progress = max(0., min(1., (now-self.transition_started)/self.transition))
        eased = progress*progress*(3-2*progress)
        return self.transition_from + (self.side-self.transition_from)*eased

    def update(self, face, frame_shape, now):
        shape = tuple(frame_shape[:2])
        target = float(face[3])*.98
        levels = square_levels(shape)
        if self.shape != shape or self.last_seen is None or not 0 <= now-self.last_seen < 5:
            self.reset()
            self.shape = shape
            self.side = min(levels, key=lambda side: abs(side-target))
        self.last_seen = now
        self.history.append(target)
        measured = float(np.median(self.history))
        current = levels.index(self.side)
        candidate = current
        while candidate < len(levels)-1 and measured > (levels[candidate]+levels[candidate+1])/2*(1+self.hysteresis):
            candidate += 1
        while candidate > 0 and measured < (levels[candidate-1]+levels[candidate])/2*(1-self.hysteresis):
            candidate -= 1
        desired = levels[candidate]
        if desired == self.side:
            self.pending = self.pending_since = None
        else:
            # Continuing towards the camera may cross several bands before any
            # hold expires. Keep time in the same direction instead of starting
            # over at every new band; reverse direction still starts fresh.
            same_direction = self.pending is not None and (desired-self.side)*(self.pending-self.side) > 0
            if not same_direction:
                self.pending_since = now
            self.pending = desired
        if self.pending is not None and now-self.pending_since >= self.hold:
            # Retarget from the current visible size, even during an active tween.
            self.transition_from = self.display_side(now)
            self.transition_started = now
            self.side = desired
            self.pending = self.pending_since = None
        return self.side


def square_bounds(face, frame_shape):
    height, width = frame_shape[:2]
    side = square_side(frame_shape, face)
    center = np.asarray(face[:2], dtype=float) + np.asarray(face[2:4], dtype=float) / 2
    center[1] -= side * 0.12  # Keep the upward offset proportional to the frame size.
    # Shift the square inside the image bounds to preserve its aspect ratio.
    x1 = int(np.clip(round(center[0] - side / 2), 5, width - side - 5))
    y1 = int(np.clip(round(center[1] - side / 2), 5, height - side - 5))
    return x1, y1, x1 + side, y1 + side


class BoxSmoother:
    """Smooth position, stabilize distance-based size; never retain identity."""

    def __init__(self, time_constant=0.08, dead_zone=2.0):
        self.time_constant = time_constant
        self.dead_zone = dead_zone
        self.history = deque(maxlen=3)
        self.sizer = DistanceSizer()
        self.reset()

    def reset(self, reset_size=True):
        self.raw_center = self.center = self.last_time = self.shape = None
        self.history.clear()
        if reset_size:
            self.sizer.reset()

    def update(self, results, frame_shape, now=None):
        # Reset the previous location and label on face loss or multiple faces.
        if len(results) != 1:
            self.reset(reset_size=False)
            return results
        now = time.monotonic() if now is None else now
        face, label, color, score = results[0]
        raw = np.asarray(face[:4], dtype=float)
        if not np.isfinite(raw).all() or np.any(raw[2:] <= 0):
            self.reset(reset_size=False)
            return results
        shape = tuple(frame_shape[:2])
        self.sizer.update(face, shape, now)
        side = self.sizer.display_side(now)
        raw_center = raw[:2] + raw[2:] / 2
        elapsed = 0 if self.last_time is None else now - self.last_time
        continuous = (self.raw_center is not None and shape == self.shape
                      and 0 < elapsed <= 0.8
                      and np.linalg.norm(raw_center - self.raw_center) <= side * 0.65)
        if continuous:
            self.history.append(raw_center.copy())
            # Small movements use the short median; deliberate motion bypasses
            # that history and uses a fast time constant to avoid trailing faces.
            moving = float(np.linalg.norm(raw_center-self.center)) > 12
            target = raw_center if moving else np.median(np.asarray(self.history), axis=0)
            delta = target - self.center
            distance = float(np.linalg.norm(delta))
            # Hold the position through jitter and follow larger movements smoothly.
            if distance > self.dead_zone:
                tau = min(self.time_constant, .025) if moving else self.time_constant
                alpha = 1 - math.exp(-min(elapsed, 0.1) / tau)
                self.center += alpha * delta * (distance - self.dead_zone) / distance
        else:
            self.history.clear()
            self.history.append(raw_center.copy())
            self.center = raw_center.copy()
        self.raw_center, self.last_time, self.shape = raw_center.copy(), now, shape
        displayed_face = np.asarray(face, dtype=np.float32).copy()
        displayed_face[:2] = self.center - side / 2
        displayed_face[2:4] = side  # Equal sides throughout the short size transition.
        # Use the label and score from the current frame.
        return [(displayed_face, label, color, score)]


class AdminTypewriter:
    """Reveal the admin label one letter at a time on a new match."""

    def __init__(self, letter_interval=0.16, profiles=None):
        self.profiles = profiles or {}
        self.letter_interval = letter_interval
        self.previous = []
        self.shape = None

    def update(self, results, frame_shape, now=None):
        now = time.monotonic() if now is None else now
        shape = tuple(frame_shape[:2])
        previous = self.previous if shape == self.shape else []
        available = set(range(len(previous)))
        current, counts = [], []
        for face, label, _color, _score in results:
            # Animate the admin label only if the current frame matches.
            if label != "Admin" and label not in self.profiles:
                counts.append(None)
                continue
            raw = np.asarray(face[:4], dtype=float)
            if not np.isfinite(raw).all() or np.any(raw[2:] <= 0):
                counts.append(None)
                continue
            center = raw[:2] + raw[2:] / 2
            candidates = [(np.linalg.norm(center - previous[index][0]), index)
                          for index in available
                          if 0 <= now - previous[index][2] <= 0.8 and previous[index][3] == label]
            started = now
            if candidates:
                distance, index = min(candidates)
                if distance <= square_side(shape) * 0.65:
                    started = previous[index][1]
                    available.remove(index)
            length = len(self.profiles[label].name) if label in self.profiles else 5
            count = min(length + 2, 1 + int(max(0, now - started) / self.letter_interval))
            counts.append(count)
            current.append((center.copy(), started, now, label))
        # Clear the animation when a face is lost or no longer matches.
        self.previous, self.shape = current, shape
        return counts


def face_frame(canvas, x1, y1, x2, y2, color, role='USER'):
    if min(x2-x1, y2-y1) < 8:
        return
    tier={'USER':0,'OPERATOR':1,'SUPERVISOR':2,'ADMIN':3}.get(role,0)
    retention, light, padding = frame_overlay(x2-x1, y2-y1, tuple(color), tier)
    origin_x, origin_y = x1-padding, y1-padding
    left, top = max(0, origin_x), max(0, origin_y)
    right = min(canvas.shape[1], origin_x+light.shape[1])
    bottom = min(canvas.shape[0], origin_y+light.shape[0])
    if left >= right or top >= bottom:
        return
    rows = slice(top-origin_y, bottom-origin_y)
    columns = slice(left-origin_x, right-origin_x)
    region = canvas[top:bottom, left:right]
    # Cached premultiplied layers: two small OpenCV operations per frame.
    tinted = cv2.multiply(region, retention[rows,columns], scale=1/255)
    cv2.add(tinted, light[rows,columns], dst=region)


@lru_cache(maxsize=24)
def frame_overlay(width, height, color, tier=0):
    """Build fixed corner artwork once per size/color; no camera data is cached."""
    short_side = min(width, height)
    weight = max(1, min(3, round(2*short_side/240)))
    padding = max(6, weight+4)
    x1 = y1 = padding
    x2, y2 = x1+width, y1+height
    side = min(x2-x1, y2-y1)
    scale = side/240
    corner = max(8, min(round(side*.17), 52))
    cut = max(2, min(9, round(side*.022)))
    inset = max(3, round(5*scale))
    weight = max(1, min(3, round(2*scale)))
    left = top = 0
    core = np.zeros((height+2*padding+1,width+2*padding+1), np.uint8)
    detail = np.zeros_like(core)
    guides = np.zeros_like(core)
    highlights = np.zeros_like(core)

    def line(mask, start, end, thickness=1):
        cv2.line(mask, (start[0]-left, start[1]-top),
                 (end[0]-left, end[1]-top), 255, thickness, cv2.LINE_AA)

    def bracket(mask, x, y, dx, dy, length, chamfer, thickness):
        points = np.array([(x+dx*length, y), (x+dx*chamfer, y),
                           (x, y+dy*chamfer), (x, y+dy*length)], np.int32)
        points -= (left, top)
        cv2.polylines(mask, [points], False, 255, thickness, cv2.LINE_AA)

    for x, y, dx, dy in ((x1,y1,1,1), (x2,y1,-1,1),
                          (x1,y2,1,-1), (x2,y2,-1,-1)):
        bracket(core, x, y, dx, dy, corner, cut, weight)
        if tier>=2:
            bracket(detail, x+dx*inset, y+dy*inset, dx, dy,
                    round(corner*.62), max(2, cut//2), 1)
        elif tier==1:
            line(detail, (x+dx*inset,y+dy*inset),
                 (x+dx*round(corner*.55),y+dy*inset))
        # A cool, bright tip gives the brackets depth without filling the face.
        line(highlights, (x+dx*round(corner*.72), y), (x+dx*corner, y))

    gap = corner+inset
    for y in (y1,y2):
        line(guides, (x1+gap,y), (x2-gap,y))
    for x in (x1,x2):
        line(guides, (x,y1+gap), (x,y2-gap))

    cx, cy = (x1+x2)//2, (y1+y2)//2
    tick = max(3, round(5*scale))
    spacing = max(6, round(side*.065))
    for x, dx in (((x1,1),(x2,-1)) if tier>=2 else ()):
        for offset in (-spacing,0,spacing):
            length = tick+2 if offset == 0 else tick
            line(detail, (x,cy+offset), (x+dx*length,cy+offset))
    for y, dy in (((y1,1),(y2,-1)) if tier>=3 else ()):
        # Fixed alignment marks; no flashing/pulsing while the face is tracked.
        half = max(3, round(4*scale))
        diamond = np.array([(cx-half,y+dy*half),(cx,y),
                            (cx+half,y+dy*half),(cx,y+dy*half*2)], np.int32)
        diamond -= (left, top)
        cv2.polylines(detail, [diamond], True, 255, 1, cv2.LINE_AA)
        if y==y1:
            # ADMIN crest: a small second chevron above the alignment diamond.
            line(detail,(cx-half*2,y+dy*half),(cx,y-dy*half))
            line(detail,(cx,y-dy*half),(cx+half*2,y+dy*half))

    alpha = np.maximum.reduce((core.astype(np.float32)*.96,
                               detail.astype(np.float32)*.65,
                               guides.astype(np.float32)*.22))/255
    halo = cv2.GaussianBlur(core, (9,9), 2).astype(np.float32)/255*.45
    shadow = cv2.dilate(np.maximum(core,detail), np.ones((3,3),np.uint8))
    shade = shadow.astype(np.float32)/255*.45
    tint = np.asarray(color,np.float32)
    shine = highlights.astype(np.float32)/255*.85
    pale = tint*.35+255*.65
    retention = (1-shade)*(1-halo)*(1-alpha)*(1-shine)
    light = tint*halo[:,:,None]
    light = light*(1-alpha[:,:,None])+tint*alpha[:,:,None]
    light = light*(1-shine[:,:,None])+pale*shine[:,:,None]
    retention = np.repeat(np.rint(retention*255).astype(np.uint8)[:,:,None],3,axis=2)
    return retention, np.clip(np.rint(light),0,255).astype(np.uint8), padding


def label_mask(label, height, letter_gap=2):
    from bitmap_font import text_mask

    return text_mask(label, height, gap=letter_gap)


def reveal_letters(glyphs, visible_letters):
    # Reserve the full label width as the letters appear.
    mask = glyphs.copy()
    if visible_letters is not None:
        occupied = np.any(mask > 0, axis=0)
        ends = np.flatnonzero(np.diff(np.pad(occupied.astype(np.int8), (1, 1))) == -1)
        count = max(0, min(int(visible_letters), len(ends)))
        stop = 0 if count == 0 else ends[count - 1]
        mask[:, stop:] = 0
    return mask


def paint_mask(canvas, glyphs, x, y):
    mask = np.pad(glyphs, 2)
    height, width = canvas.shape[:2]
    x, y = int(x), int(y)
    if x >= width or y >= height:
        return
    # Clip the label safely on both sides of the image.
    left, top = max(0, -x), max(0, -y)
    mask = mask[top:height - y, left:width - x]
    x, y = max(0, x), max(0, y)
    if not mask.size:
        return
    region = canvas[y:y + mask.shape[0], x:x + mask.shape[1]]
    outline = cv2.dilate(mask, np.ones((3, 3), np.uint8))
    shadow_alpha = outline[..., None].astype(np.float32) / 255
    alpha = mask[..., None].astype(np.float32) / 255
    shaded = region * (1 - shadow_alpha) + np.asarray(DARK) * shadow_alpha
    region[:] = np.rint(shaded * (1 - alpha) + np.asarray(WHITE) * alpha).astype(np.uint8)


def identity_label(canvas, label, x1, y1, x2, y2, visible_letters=None):
    height, width = canvas.shape[:2]
    text_height = max(10, round(min(height, width) * 17 / 480))
    glyphs = label_mask(label, text_height)
    th, tw = glyphs.shape[0] + 4, glyphs.shape[1] + 4
    x = x2 + 16
    y = y1 + round((y2 - y1) * 0.25)
    if x + tw > width - 4:
        x = max(4, x2 - tw - 10)
    y = max(4, min(y, height - th - 4))
    x = max(0, min(x, width - tw))
    paint_mask(canvas, glyphs, x, y)
    return x, y, tw, th


def profile_label(canvas, x1, y1, x2, y2, stage=None, profile=None):
    height, width = canvas.shape[:2]
    scale = min(height, width) / 480
    # Keep the name's original text size and reserve room for Turkish diacritics.
    length = 5 if profile is None else len(profile.name)
    if profile is None:
        name = reveal_letters(label_mask("ADMIN", max(14, round(27 * scale))),
                              None if stage is None else min(stage, length))
        role = label_mask("Role: ADMIN", max(7, round(10 * scale)), letter_gap=1)
        field = label_mask("Field: Unspecified", max(7, round(10 * scale)), letter_gap=1)
    else:
        from bitmap_font import text_mask, fields_mask
        max_width = max(80, round(width * 0.34))
        name = text_mask(profile.name, max(10, round(17 * scale)), gap=2, max_width=max_width,
                         visible=None if stage is None else min(stage, length))
        role = text_mask("Role: " + profile.role, max(7, round(10 * scale)), max_width=max_width)
        field = fields_mask(profile.fields, max(7, round(10 * scale)), max_width)
    gap = max(5, round(8 * scale))
    row_gap = max(4, round(6 * scale))
    offsets = [0, name.shape[0] + 4 + gap,
               name.shape[0] + 4 + gap + role.shape[0] + 4 + row_gap]
    block_width = max(name.shape[1], role.shape[1], field.shape[1]) + 4
    block_height = offsets[2] + field.shape[0] + 4
    x = x2 + 16
    y = y1 + round((y2 - y1) * 0.25)
    # Reserve space for the whole profile so later rows do not shift it.
    if x + block_width > width - 4:
        x = max(4, x2 - block_width - 10)
    x = max(0, min(x, max(0, width - block_width)))
    y = max(4, min(y, max(4, height - block_height - 4)))
    paint_mask(canvas, name, x, y)
    if stage is None or stage >= length + 1:
        paint_mask(canvas, role, x, y + offsets[1])
    if stage is None or stage >= length + 2:
        paint_mask(canvas, field, x, y + offsets[2])
    return x, y, block_width, block_height


def draw_hud(frame, results, admin_letters=None, profiles=None):
    profiles = profiles or {}
    # Draw on a copy to preserve the frame used for inference.
    canvas = frame.copy()
    height, width = frame.shape[:2]
    for index, (face, label, _, _score) in enumerate(results):
        x, y, w, h = (float(value) for value in face[:4])
        if not all(np.isfinite((x, y, w, h))) or w <= 0 or h <= 0:
            continue
        x1, y1, x2, y2 = square_bounds(face, frame.shape)
        if x2 - x1 < 24 or y2 - y1 < 24:
            continue
        known = label == "Admin" or label in profiles
        color = BLUE if known else RED if label == "Unknown" else AMBER
        name = "ADMIN" if label == "Admin" else "UNKNOWN" if label == "Unknown" else "UNCLEAR"
        role=profiles[label].role if label in profiles else 'ADMIN' if label=='Admin' else 'USER'
        face_frame(canvas, x1, y1, x2, y2, color, role)
        visible = None if admin_letters is None else admin_letters[index]
        if known:
            profile_label(canvas, x1, y1, x2, y2, visible, profiles.get(label))
        else:
            identity_label(canvas, name, x1, y1, x2, y2)
    return canvas


def demo_frame():
    # Synthetic face outline for visual checks.
    frame = np.full((480, 640, 3), (32, 30, 27), dtype=np.uint8)
    cv2.ellipse(frame, (280, 240), (83, 116), 0, 0, 360, (88, 86, 78), 2, cv2.LINE_AA)
    cv2.ellipse(frame, (280, 468), (144, 112), 0, 180, 360, (88, 86, 78), 2, cv2.LINE_AA)
    face = np.array([193, 118, 174, 246, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0.99], dtype=np.float32)
    return frame, face
