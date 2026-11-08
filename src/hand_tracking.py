"""Local hand landmarks and held poses; never executes actions or grants access."""
from project_paths import PROJECT_ROOT
from dataclasses import dataclass
from finger_count import number_pose
from session_pose import session_pose,thumb_three
from pathlib import Path
import math
import time
import cv2
import numpy as np

MODEL = PROJECT_ROOT / 'models' / 'gesture_recognizer.task'
GESTURES = {'Open_Palm': 'OPEN PALM', 'Closed_Fist': 'FIST',
            'Pointing_Up': 'POINT UP', 'Victory': 'VICTORY',
            'Thumb_Up': 'THUMBS UP', 'Thumb_Down': 'THUMBS DOWN',
            'ILoveYou': 'I LOVE YOU'}
CONNECTIONS = ((0,1),(1,2),(2,3),(3,4),(0,5),(5,6),(6,7),(7,8),
               (5,9),(9,10),(10,11),(11,12),(9,13),(13,14),(14,15),
               (15,16),(13,17),(0,17),(17,18),(18,19),(19,20))


@dataclass
class Hand:
    number: int
    points: np.ndarray  # mirrored camera pixels, measured in this frame
    gesture: str = ''
    score: float = 0.
    fingers: int | None = None
    raw_points: np.ndarray | None = None  # Unsmooth measurements for the control filter.
    session_start: bool = False


def decode_result(result, shape):
    """Do not invent a pose for a missing/low-confidence classifier result."""
    h, w = shape[:2]
    hands = []
    for index, landmarks in enumerate(result.hand_landmarks[:2]):
        points = np.asarray([(w-1-p.x*w, p.y*h) for p in landmarks], dtype=np.float32)
        if points.shape != (21, 2) or not np.isfinite(points).all():
            continue
        categories = result.gestures[index] if index < len(result.gestures) else []
        category = max(categories, key=lambda c: c.score) if categories else None
        score = float(category.score) if category is not None else 0.
        gesture = category.category_name if category is not None else ''
        if gesture not in GESTURES or not math.isfinite(score) or score < .65:
            gesture, score = '', 0.
        world=getattr(result,'hand_world_landmarks',())
        if index<len(world):
            geometry=np.asarray([(p.x,p.y,p.z) for p in world[index]],dtype=np.float32)
        else:
            geometry=np.asarray([(p.x*w,p.y*h,getattr(p,'z',0.)*w) for p in landmarks],dtype=np.float32)
        session=session_pose(geometry,points) if gesture not in ('ILoveYou','Thumb_Up','Thumb_Down','Closed_Fist') else False
        count=number_pose(geometry,gesture,points)
        if session or (count==3 and thumb_three(geometry,points)):count=None
        hands.append(Hand(0,points,gesture,score,count,session_start=session))
    return hands


class HandPresentation:
    """Brief pose confirmation and light position smoothing, with no lost-hand hold."""
    def __init__(self):
        self.previous = []
        self.shape = None

    def reset(self):
        self.previous = []
        self.shape = None

    def update(self, hands, shape, now):
        shape = tuple(shape[:2])
        if shape != self.shape:
            self.reset()
            self.shape = shape
        remaining = list(self.previous)
        current, visible = [], []
        diagonal = math.hypot(*shape)
        # Nearest wrist assignment handles model output order changes.
        pairs = sorted((float(np.linalg.norm(hand.points[0]-old['raw'][0]))/diagonal, i, j)
                       for i, hand in enumerate(hands) for j, old in enumerate(remaining))
        matches, used = {}, set()
        for distance, i, j in pairs:
            if distance < .22 and i not in matches and j not in used and 0 <= now-remaining[j]['time'] < .3:
                matches[i] = remaining[j]
                used.add(j)
        numbers = {old['number'] for old in matches.values()}
        for i, hand in enumerate(hands):
            old = matches.get(i)
            if old is None:
                number = next(n for n in (1,2) if n not in numbers)
                numbers.add(number)
                since, points = now, hand.points.copy()
                count_since=now
                session_since=now
            else:
                number = old['number']
                since = old['since'] if hand.gesture == old['candidate'] else now
                count_since=old['count_since'] if hand.fingers==old['fingers'] else now
                session_since=old['session_since'] if hand.session_start==old['session_start'] else now
                alpha = 1-math.exp(-max(.001, now-old['time'])/.035)
                points = old['points'] + alpha*(hand.points-old['points'])
            confirmed = hand.gesture if now-since >= .12 else ''
            current.append(dict(number=number, raw=hand.points.copy(), points=points,
                                candidate=hand.gesture, since=since, time=now,
                                fingers=hand.fingers,count_since=count_since,
                                session_start=hand.session_start,session_since=session_since))
            fingers=hand.fingers if now-count_since>=.16 else None
            visible.append(Hand(number,points,confirmed,hand.score if confirmed else 0.,fingers,hand.points.copy(),
                                hand.session_start and now-session_since>=.16))
        self.previous = current  # Empty/missing hands clear immediately.
        return visible


class HandTracker:
    def __init__(self, model_path=MODEL):
        if not Path(model_path).is_file():
            raise FileNotFoundError('Hand model missing: run tools/download_hand_model.py.')
        import mediapipe as mp
        self.mp = mp
        options = mp.tasks.vision.GestureRecognizerOptions(
            base_options=mp.tasks.BaseOptions(model_asset_path=str(model_path)),
            running_mode=mp.tasks.vision.RunningMode.VIDEO, num_hands=2,
            min_hand_detection_confidence=.6, min_hand_presence_confidence=.6,
            min_tracking_confidence=.6,
            canned_gesture_classifier_options=mp.tasks.components.processors.ClassifierOptions(
                max_results=1))
        self.model = mp.tasks.vision.GestureRecognizer.create_from_options(options)
        self.last_stamp = -1
        self.presentation = HandPresentation()

    def measure(self, frame, now=None):
        now = time.monotonic() if now is None else now
        stamp = max(self.last_stamp+1, int(now*1000))
        self.last_stamp = stamp
        image = self.mp.Image(image_format=self.mp.ImageFormat.SRGB,
                              data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        result = self.model.recognize_for_video(image, stamp)
        return self.presentation.update(decode_result(result, frame.shape), frame.shape, now)

    def reset(self):
        self.presentation.reset()

    def close(self):
        self.model.close()


def draw_hands(frame, hands):
    """Thin blue skeleton, fingertip lights and a small pose label, on the same view."""
    h, w = frame.shape[:2]
    for hand in hands:
        points = np.rint(hand.points).astype(int)
        points[:, 0] = np.clip(points[:, 0], 0, w-1)
        points[:, 1] = np.clip(points[:, 1], 0, h-1)
        for a, b in CONNECTIONS:
            cv2.line(frame, tuple(points[a]), tuple(points[b]), (65, 30, 12), 3, cv2.LINE_AA)
            cv2.line(frame, tuple(points[a]), tuple(points[b]), (240, 165, 65), 1, cv2.LINE_AA)
        for index, point in enumerate(points):
            tip = index in (4, 8, 12, 16, 20)
            cv2.circle(frame, tuple(point), 3 if tip else 2, (250, 213, 145), -1, cv2.LINE_AA)
        text = f'HAND {hand.number:02d} / ' + GESTURES.get(hand.gesture, 'TRACKING')
        if hand.session_start:text=f'HAND {hand.number:02d} / SESSION START'
        if hand.fingers is not None:text+=f' / {hand.fingers}'
        (tw, th), baseline = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, .42, 1)
        x = max(4, min(w-tw-12, int(points[:,0].min())))
        y = max(th+12, min(h-baseline-6, int(points[:,1].min())-12))
        cv2.rectangle(frame, (x-4,y-th-5), (min(w-1,x+tw+5),y+baseline+4), (26,19,12), -1)
        cv2.putText(frame, text, (x,y), cv2.FONT_HERSHEY_SIMPLEX, .42, (250,206,137), 1, cv2.LINE_AA)
    return frame
