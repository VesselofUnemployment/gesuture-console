"""Prepare camera frames for display without modifying enrollment data."""
import cv2
import numpy as np


def fitted_size(frame_shape, width, height):
    source_h, source_w = frame_shape[:2]
    width, height = max(1, int(width)), max(1, int(height))
    factor = min(width/source_w, height/source_h)
    return min(width, max(1, round(source_w*factor))), min(height, max(1, round(source_h*factor)))


def mirrored_face(face, width):
    result = np.asarray(face).copy()
    result[0] = width - float(face[0]) - float(face[2])
    # Draw from the mirrored box and retain the original landmarks for inference.
    return result


def mirrored_results(results, width):
    return [(mirrored_face(face, width), label, color, score)
            for face, label, color, score in results]


def registration_preview(frame, faces):
    display = cv2.flip(frame, 1)
    if faces is not None:
        for face in faces:
            x, y, w, h = mirrored_face(face, frame.shape[1])[:4].astype(int)
            cv2.rectangle(display, (x, y), (x+w, y+h), (238, 196, 118), 2)
    return display


def letterbox(frame, width, height):
    width, height = max(1, int(width)), max(1, int(height))
    fw, fh = fitted_size(frame.shape, width, height)
    interpolation = cv2.INTER_AREA if fw < frame.shape[1] else cv2.INTER_LINEAR
    image = cv2.resize(frame, (fw, fh), interpolation=interpolation)
    canvas = np.zeros((height, width, 3), dtype=frame.dtype)
    x, y = (width-fw)//2, (height-fh)//2
    canvas[y:y+fh, x:x+fw] = image
    return canvas
