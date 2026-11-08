"""Cheap dark/uniform frame check before running any vision model."""
import numpy as np


class CameraIdle:
    interval = .25

    def __init__(self):
        self.paused = False
        self.mean = self.p95 = self.deviation = 0.

    def update(self, frame):
        # About 1,200 sampled pixels, no full-frame copies or neural inference.
        height, width = frame.shape[:2]
        pixels = frame[::max(1, height//30), ::max(1, width//40)].astype(np.float32)
        # Strongest channel prevents a saturated dark-blue/red scene looking black.
        light = pixels.max(axis=2) if pixels.ndim == 3 else pixels
        self.mean = float(light.mean())
        self.p95 = float(np.percentile(light, 95))
        self.deviation = float(light.std())
        if self.paused:
            # Hysteresis prevents mode flicker around the entry thresholds.
            self.paused = self.mean <= 32 and self.p95 <= 44 and self.deviation <= 8
        else:
            self.paused = self.mean <= 26 and self.p95 <= 36 and self.deviation <= 6
        return self.paused

    def stats(self):
        return dict(mean=self.mean, p95=self.p95, deviation=self.deviation)
