"""Step 2: Face enrollment helpers and the registration entry point."""
from project_paths import PROJECT_ROOT
import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

BASE = PROJECT_ROOT
ADMIN_FILE = BASE / "data" / "admin.npz"
WINDOW = "Step 2 - Admin Enrollment"
POSES = (
    "Without headphones: face forward and press K",
    "Without headphones: turn slightly right and press K",
    "Without headphones: turn slightly left and press K",
    "With headphones: face forward and press K",
    "With headphones: turn slightly right and press K",
    "With headphones: turn slightly left and press K",
)
SAMPLE_COUNT = len(POSES)
# Baseline similarity threshold from the OpenCV example.
CONSISTENCY_THRESHOLD = 0.363


def load_models():
    detector_path = BASE / "models" / "face_detection_yunet_2023mar.onnx"
    recognizer_path = BASE / "models" / "face_recognition_sface_2021dec.onnx"
    if not detector_path.is_file() or not recognizer_path.is_file():
        raise RuntimeError("Face models are missing. Run tools/download_face_models.py first.")
    detector = cv2.FaceDetectorYN.create(str(detector_path), "", (640, 480), 0.9, 0.3, 5000)
    recognizer = cv2.FaceRecognizerSF.create(str(recognizer_path), "")
    return detector, recognizer


def select_face(faces, frame_shape):
    # Require exactly one face during enrollment.
    if faces is None or len(faces) == 0:
        return None, "No face detected"
    if len(faces) != 1:
        return None, "Only one person can be in view during enrollment"
    face = faces[0]
    x, y, width, height = face[:4]
    frame_height, frame_width = frame_shape[:2]
    if width < 100 or height < 100:
        return None, "Move a little closer to the camera"
    if x < 0 or y < 0 or x + width > frame_width or y + height > frame_height:
        return None, "Keep your entire face in view"
    return face, ""


def normalize(feature):
    feature = np.asarray(feature, dtype=np.float32).reshape(-1).copy()
    length = np.linalg.norm(feature)
    if feature.size != 128 or not np.isfinite(feature).all() or length < 1e-8:
        raise RuntimeError("Could not extract a valid face embedding.")
    return feature / length


def validate_templates(templates, expected_count=None):
    templates = np.asarray(templates, dtype=np.float32)
    # Legacy enrollment files can contain three or six samples.
    if templates.ndim != 2 or templates.shape[1] != 128 or templates.shape[0] not in (3, SAMPLE_COUNT):
        raise RuntimeError("Invalid admin record.")
    if expected_count is not None and templates.shape[0] != expected_count:
        raise RuntimeError(f"Enrollment requires {expected_count} samples.")
    if not np.isfinite(templates).all():
        raise RuntimeError("Invalid admin record.")
    if not np.allclose(np.linalg.norm(templates, axis=1), 1.0, atol=1e-4):
        raise RuntimeError("Invalid admin face embeddings.")
    return templates


def save_admin(samples, path=ADMIN_FILE):
    templates = validate_templates(np.stack(samples), expected_count=SAMPLE_COUNT)
    path.parent.mkdir(exist_ok=True)
    # Exclusive creation protects existing records.
    with path.open("xb") as output:
        try:
            np.savez_compressed(output, version=np.array(1), model=np.array("sface_2021dec"),
                                role=np.array("admin"), templates=templates)
        except Exception:
            output.close()
            path.unlink(missing_ok=True)
            raise


def load_admin(path=ADMIN_FILE):
    with np.load(path, allow_pickle=False) as record:
        if record["version"].item() != 1 or record["model"].item() != "sface_2021dec":
            raise RuntimeError("This version does not support the admin record format.")
        if record["role"].item() != "admin":
            raise RuntimeError("Admin role marker is missing.")
        return validate_templates(record["templates"]).copy()


def check_setup(detector, recognizer):
    # Check model loading, saving, and record protection with synthetic data.
    import tempfile
    blank = np.zeros((480, 640, 3), dtype=np.uint8)
    detector.setInputSize((640, 480))
    _, faces = detector.detect(blank)
    assert faces is None or len(faces) == 0
    assert select_face(None, blank.shape)[0] is None
    fake_face = np.zeros((1, 15), dtype=np.float32)
    fake_face[0, :4] = (100, 100, 150, 150)
    assert select_face(np.repeat(fake_face, 2, axis=0), blank.shape)[0] is None
    assert select_face(fake_face, blank.shape)[0] is not None
    # Run SFace inference on a synthetic image.
    sample = normalize(recognizer.feature(np.zeros((112, 112, 3), dtype=np.uint8)))
    with tempfile.TemporaryDirectory(prefix="cv_admin_check_") as temporary:
        path = Path(temporary) / "test_admin.npz"
        # Reject incomplete enrollments before writing a file.
        try:
            save_admin([sample.copy() for _ in range(SAMPLE_COUNT - 1)], path)
        except RuntimeError:
            pass
        else:
            raise RuntimeError("The enrollment accepted an incomplete record.")
        assert not path.exists()
        save_admin([sample.copy() for _ in range(SAMPLE_COUNT)], path)
        assert np.allclose(load_admin(path), np.stack([sample] * SAMPLE_COUNT))
        before = path.read_bytes()
        try:
            save_admin([sample] * SAMPLE_COUNT, path)
        except FileExistsError:
            pass
        else:
            raise RuntimeError("The existing record protection check failed.")
        assert path.read_bytes() == before
    print("Ready: face models, embedding extraction, and record protection passed.")
    print("The check used synthetic records with the camera closed.")


def main():
    # Use the standard registration wizard for interactive enrollment.
    if "--check" in sys.argv:
        detector, recognizer = load_models()
        check_setup(detector, recognizer)
        return 0
    import register_ui
    register_ui.main()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RuntimeError, OSError, ValueError, KeyError, cv2.error) as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nStopped. Incomplete registrations are discarded.")
