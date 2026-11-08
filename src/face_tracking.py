"""Local measured face landmarks for the visual recognition overlay."""
from project_paths import PROJECT_ROOT
from pathlib import Path
import time
import cv2
import numpy as np

MODEL = PROJECT_ROOT / 'models' / 'face_landmarker.task'
# Sparse points retain the reference's open triangular look.
NODES = (109, 338, 234, 454, 172, 397, 152,
         33, 263, 133, 362, 168, 1, 61, 291, 17)


def matches_face(points, face):
    """Do not attach a mesh to a different detector result."""
    points = np.asarray(points)
    if points.shape != (478, 2) or not np.isfinite(points).all():
        return False
    x, y, w, h = map(float, face[:4])
    center = np.median(points, axis=0)
    if w <= 0 or h <= 0 or not (x <= center[0] <= x+w and y <= center[1] <= y+h):
        return False
    lo, hi = points.min(axis=0), points.max(axis=0)
    intersection = np.maximum(0, np.minimum(hi, (x+w, y+h))-np.maximum(lo, (x, y)))
    overlap = float(np.prod(intersection))
    union = float(np.prod(hi-lo)) + w*h - overlap
    return union > 0 and overlap/union >= .25


def mirror_points(points, width):
    result = np.asarray(points, dtype=np.float32).copy()
    result[:, 0] = width - 1 - result[:, 0]
    return result


class FaceTracker:
    def __init__(self, model_path=MODEL):
        import mediapipe as mp
        self.mp = mp
        options = mp.tasks.vision.FaceLandmarkerOptions(
            base_options=mp.tasks.BaseOptions(model_asset_path=str(model_path)),
            running_mode=mp.tasks.vision.RunningMode.VIDEO,
            num_faces=1, min_face_detection_confidence=.6,
            min_face_presence_confidence=.6, min_tracking_confidence=.6,
            output_face_blendshapes=False, output_facial_transformation_matrixes=False)
        self.model = mp.tasks.vision.FaceLandmarker.create_from_options(options)
        self.last_stamp = -1

    def measure(self, frame, face, now=None):
        now = time.monotonic() if now is None else now
        stamp = max(self.last_stamp+1, int(now*1000))
        self.last_stamp = stamp
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        image = self.mp.Image(image_format=self.mp.ImageFormat.SRGB, data=rgb)
        result = self.model.detect_for_video(image, stamp)
        if len(result.face_landmarks) != 1:
            return None
        h, w = frame.shape[:2]
        points = np.asarray([(p.x*w, p.y*h) for p in result.face_landmarks[0]], dtype=np.float32)
        return points if matches_face(points, face) else None

    def close(self):
        self.model.close()
