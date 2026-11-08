"""Step 3: Live recognition and the management terminal."""
import argparse
import hashlib
import importlib
import sys

from vision_hud import BLUE, RED
from person_store import load_people, match_person

import cv2
import numpy as np

# Reuse the enrollment models and storage helpers.
enrollment = importlib.import_module("face_enrollment")
# The initial match threshold is 0.65. Multi-person calibration is pending.
DEFAULT_THRESHOLD = 0.65


def threshold_value(value):
    number = float(value)
    if not np.isfinite(number) or not 0 < number <= 1:
        raise argparse.ArgumentTypeError("Threshold must be greater than 0 and at most 1.")
    return number


def select_recognition_face(face, frame_shape):
    """Enrollment requires a wide frontal crop; recognition accepts narrower
    measured detections, while preserving height, crop and quality checks.
    This never changes the embedding threshold or person-to-person margin.
    """
    face = np.asarray(face, dtype=np.float32)
    if face.ndim != 1 or face.size < 15 or not np.isfinite(face).all():
        return None
    x, y, width, height = face[:4]
    frame_height, frame_width = frame_shape[:2]
    if width < 60 or height < 100:
        return None
    if x < 0 or y < 0 or x+width > frame_width or y+height > frame_height:
        return None
    return face


def analyze_face(frame, face, recognizer, people, threshold, details=None, continuation=None):
    metric=dict(identity='Face unclear',score=None,blur=None,brightness=None,reason='SIZE_OR_CROP')
    def result(label,color,score):
        metric.update(identity=label,score=score)
        if details is not None: details.append(metric)
        return label,color,score
    selected = select_recognition_face(face, frame.shape)
    if selected is None:
        if continuation is not None: continuation.reset()
        return result('Face unclear',(0,165,255),None)
    aligned=recognizer.alignCrop(frame,selected)
    gray=cv2.cvtColor(aligned,cv2.COLOR_BGR2GRAY)
    blur,brightness=float(cv2.Laplacian(gray,cv2.CV_64F).var()),float(gray.mean())
    metric.update(blur=blur,brightness=brightness)
    if blur<25 or not 35<=brightness<=220:
        if continuation is not None: continuation.reset()
        metric['reason']='BLUR' if blur<25 else 'LIGHTING'
        return result('Face unclear',(0,165,255),None)
    feature=recognizer.feature(aligned)
    if continuation is not None:
        person,score=continuation.match(feature,selected,people,threshold)
        metric.update(continuation.decision)
        return result(person.identity if person is not None else 'Unknown', BLUE if person is not None else RED, score)
    person,score=match_person(feature,people,threshold)
    if person is not None:
        metric['reason']='MATCHED'
        return result(person.identity,BLUE,score)
    metric['reason']='NO_ACTIVE_PROFILES' if score is None else 'AMBIGUOUS' if score>=threshold else 'BELOW_THRESHOLD'
    return result('Unknown',RED,score)


def check_setup(detector, recognizer, people, threshold):
    paths = list(enrollment.BASE.glob("data/**/*.npz"))
    before = {path: hashlib.sha256(path.read_bytes()).digest() for path in paths}
    blank = np.zeros((480, 640, 3), dtype=np.uint8)
    detector.setInputSize((640, 480))
    _, faces = detector.detect(blank)
    assert faces is None or len(faces) == 0
    for person in people:
        if not person.active:
            continue
        for sample in person.templates:
            match, score = match_person(sample, people, threshold)
            assert match is not None and match.identity == person.identity and score > 0.999
    for invalid in ("nan", "inf", "0", "-1", "1.01"):
        try:
            threshold_value(invalid)
        except argparse.ArgumentTypeError:
            pass
        else:
            raise RuntimeError("Invalid threshold accepted.")
    frame = np.random.default_rng(0).integers(70, 180, size=(480, 640, 3), dtype=np.uint8)
    face = np.array([100, 100, 160, 180, 145, 160, 210, 160,
                     178, 192, 150, 228, 207, 228, 0.99], dtype=np.float32)
    label, color, score = analyze_face(frame, face, recognizer, people, threshold)
    assert label in [p.identity for p in people] + ["Unknown"]
    assert (score is not None) == any(person.active for person in people)
    tiny = face.copy()
    tiny[2:4] = 50
    assert analyze_face(frame, tiny, recognizer, people, threshold)[0] == "Face unclear"
    assert analyze_face(blank, face, recognizer, people, threshold)[2] is None
    for path, digest in before.items():
        assert hashlib.sha256(path.read_bytes()).digest() == digest
    print(f"Ready: {len(people)} profile(s). Model, matching and record preservation checks passed.")
    print("Camera was not opened. Live recognition accuracy has not been measured.")


def main():
    parser = argparse.ArgumentParser(description="Live recognition and management terminal")
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--threshold", type=threshold_value, default=DEFAULT_THRESHOLD)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--background", action="store_true", help="Start hidden in the Windows tray")
    args = parser.parse_args()
    if args.check:
        from profile_store import ProfileRepository
        people = ProfileRepository.inspect_people()
        detector, recognizer = enrollment.load_models()
        check_setup(detector, recognizer, people, args.threshold)
        return 0
    from runtime_lifecycle import configure_logging
    configure_logging()
    from background_runtime import SingleInstance
    with SingleInstance() as instance:
        if not instance.primary:
            if not args.background:instance.activate()
            return 0
        people = load_people()
        if not people:
            raise RuntimeError("No profiles found. Set role passwords in settings.cmd, then enroll with register.cmd.")
        detector, recognizer = enrollment.load_models()
        from recognition_ui import run_recognition
        run_recognition(args.camera, args.threshold, detector, recognizer,args.background,instance)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RuntimeError, OSError, ValueError, KeyError, cv2.error) as error:
        if sys.stderr is not None:print(f"Error: {error}", file=sys.stderr)
        else:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None,str(error),'Vision Control',0x10)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nStopped.")
