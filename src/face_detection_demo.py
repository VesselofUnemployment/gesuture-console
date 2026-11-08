"""Step 1: A standalone camera face detection demo."""

import argparse
import sys
from pathlib import Path

import cv2
from runtime_lifecycle import CameraLease


WINDOW = "Step 1 - Face Detection"


def load_detector():
    # Load a pretrained face detection model.
    model_path = Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"
    detector = cv2.CascadeClassifier(str(model_path))
    if detector.empty():
        raise RuntimeError("Could not load the face detection model.")
    return detector


def find_faces(detector, frame):
    # Process one video frame at a time.
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    gray = cv2.equalizeHist(gray)
    return detector.detectMultiScale(
        gray, scaleFactor=1.1, minNeighbors=5, minSize=(60, 60)
    )


def main():
    parser = argparse.ArgumentParser(description="Face detection demo")
    parser.add_argument("--camera", type=int, default=0, help="Camera index")
    parser.add_argument("--check", action="store_true", help="Check the setup without opening the camera")
    args = parser.parse_args()
    detector = load_detector()

    if args.check:
        import numpy as np
        blank = np.zeros((480, 640, 3), dtype=np.uint8)
        faces = find_faces(detector, blank)
        if len(faces) != 0:
            raise RuntimeError("Unexpected face detection in a blank frame.")
        print(f"Ready: OpenCV {cv2.__version__}, the model, and image processing passed.")
        return 0

    # Camera 0 is usually the built-in camera.
    lease = CameraLease(args.camera)
    camera = None
    try:
        camera = cv2.VideoCapture(args.camera, cv2.CAP_DSHOW)
        if not camera.isOpened():
            camera.release()
            camera = cv2.VideoCapture(args.camera)
        if not camera.isOpened():
            raise RuntimeError(
                "Could not open the camera. Close other applications using it. "
                "Check Windows camera privacy settings. "
                "Try --camera 1 to select a different camera."
            )
        camera.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        print("Camera opened. Focus the window and press Q or Esc to exit.")
        while True:
            ok, frame = camera.read()
            if not ok:
                raise RuntimeError("Could not read a camera frame.")
            frame = cv2.flip(frame, 1)  # Mirror the preview.
            faces = find_faces(detector, frame)
            for x, y, width, height in faces:
                cv2.rectangle(frame, (x, y), (x + width, y + height), (0, 220, 0), 2)
                cv2.putText(frame, "Face detected", (x, max(25, y - 10)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 220, 0), 2)
            cv2.putText(frame, f"Faces: {len(faces)} | Exit: Q / Esc", (15, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
            cv2.imshow(WINDOW, frame)
            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), ord("Q"), 27):
                break
            if cv2.getWindowProperty(WINDOW, cv2.WND_PROP_VISIBLE) < 1:
                break
    finally:
        try:
            if camera is not None:
                camera.release()
        finally:
            lease.close()
            cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RuntimeError, cv2.error) as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nStopped.")
