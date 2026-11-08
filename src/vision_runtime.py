"""Camera measurements and per-session recognition settings."""
from collections import deque, Counter
import math
import threading
import time
from app_launcher import DEFAULT_BINDINGS


class VisionDiagnostics:
    def __init__(self, camera_index=0, threshold=0.65):
        self.lock = threading.RLock()
        self.threshold = float(threshold)
        self.debug = False
        self.camera_index = camera_index
        self.state = 'STARTING'
        self.error = ''
        self.frames = 0
        self.last_frame = None
        self.times = deque(maxlen=40)
        self.resolution = (0, 0)
        self.processing_ms = 0.0
        self.faces = []
        self.tracking = 'STARTING'
        self.tracking_ms = 0.
        self.landmarks = 0
        self.animation = 'IDLE'
        self.tracking_error = ''
        self.hands_enabled = True
        self.hands_state = 'STARTING'
        self.hands_ms = 0.
        self.hands = []
        self.hands_error = ''
        self.apps_enabled = True
        self.app_action = ''
        self.app_shortcuts = DEFAULT_BINDINGS
        self.camera_paused = False
        self.camera_light = dict(mean=0., p95=0., deviation=0.)
        self.latency = {}
        self.samples = {}
        self.counters = {}
        self.record_errors = {}
        self.face_events = deque(maxlen=512)

    def set_latency(self, **values):
        with self.lock:
            for key, value in values.items():
                value = max(0., float(value))
                if not math.isfinite(value):
                    continue
                self.latency[key] = value
                self.samples.setdefault(key, deque(maxlen=256)).append(value)

    def set_counters(self, **values):
        with self.lock:
            self.counters.update(values)

    def set_record_errors(self, errors):
        with self.lock:
            self.record_errors = dict(errors)

    def performance(self):
        # Called by an explicit status request, never sorted on every frame.
        with self.lock:
            samples = {key: sorted(values) for key, values in self.samples.items()}
        return {key: dict(count=len(values), median=values[len(values)//2],
                          p95=values[min(len(values)-1, math.ceil(len(values)*.95)-1)],
                          p99=values[min(len(values)-1, math.ceil(len(values)*.99)-1)])
                for key, values in samples.items() if values}

    def set_camera_idle(self, paused, stats):
        with self.lock:
            if self.camera_paused != bool(paused):self.times.clear()
            self.camera_paused = bool(paused)
            self.camera_light = dict(stats)

    def set_apps_enabled(self,enabled):
        with self.lock:self.apps_enabled=bool(enabled)

    def set_app_action(self,text):
        with self.lock:self.app_action=str(text)

    def set_hands_enabled(self, enabled):
        with self.lock:
            self.hands_enabled = bool(enabled)
            self.hands = []
            self.hands_state = 'STARTING' if enabled else 'OFF'
            self.hands_ms = 0.

    def set_hands(self, state, processing_ms=0., hands=(), error=''):
        with self.lock:
            if not self.hands_enabled:
                state, processing_ms, hands = 'OFF', 0., ()
            self.hands_state, self.hands_ms = state, processing_ms
            self.hands = [dict(number=h.number, gesture='SESSION START' if getattr(h,'session_start',False) else (h.gesture or 'TRACKING'), score=h.score,
                               fingers=h.fingers) for h in hands]
            self.hands_error = error

    def record(self, frame_shape, faces, processing_ms, now=None, captured=None):
        now = time.monotonic() if now is None else now
        with self.lock:
            self.state, self.error = ('PAUSED / DARK CAMERA' if self.camera_paused else 'ONLINE'), ''
            self.frames += 1
            self.last_frame = now if captured is None else captured
            self.times.append(now)
            self.resolution = (frame_shape[1], frame_shape[0])
            self.processing_ms = processing_ms
            self.faces = [dict(face) for face in faces]
            if len(faces)>1:
                event=('MULTIPLE_FACES',None)
            elif faces:
                event=(faces[0].get('reason','UNKNOWN'),faces[0].get('score'))
            else:
                event=('DARK' if self.camera_paused else 'NO_FACE',None)
            self.face_events.append(event)

    def face_summary(self):
        with self.lock:
            events=list(self.face_events)
        scores=sorted(float(score) for _,score in events if score is not None and math.isfinite(score))
        return dict(window_frames=len(events), reasons=dict(Counter(reason for reason,_ in events)),
                    score_count=len(scores), minimum=scores[0] if scores else None,
                    median=scores[len(scores)//2] if scores else None,
                    maximum=scores[-1] if scores else None)

    def export_face_summary(self):
        # Explicit local request only: no frame-by-frame disk writes, identities,
        # images, landmarks or embeddings in this fixed-path anonymous summary.
        import os, json
        from project_paths import PROJECT_ROOT
        path=PROJECT_ROOT/'.local'/'diagnostics'/'recognition-summary.json'
        path.parent.mkdir(parents=True,exist_ok=True)
        state=self.snapshot()
        result=dict(pid=os.getpid(),created=time.time(),state=state['state'],frames=state['frames'],
                    threshold=state['threshold'],summary=self.face_summary())
        pending=path.with_suffix('.pending')
        pending.write_text(json.dumps(result,indent=2),encoding='utf-8')
        pending.replace(path)
        return result

    def set_tracking(self, state, processing_ms, landmarks, animation, error=''):
        with self.lock:
            self.tracking, self.tracking_ms = state, processing_ms
            self.landmarks, self.animation = landmarks, animation
            self.tracking_error = error

    def fail(self, error):
        with self.lock:
            self.state, self.error = 'ERROR', str(error)
            self.faces = []
            self.hands = []
            self.hands_state = 'UNAVAILABLE'

    def set_debug(self, enabled):
        with self.lock: self.debug = bool(enabled)

    def set_threshold(self, value):
        try: value = float(value)
        except (TypeError, ValueError): raise ValueError('Threshold must be a number between 0 and 1.') from None
        if not math.isfinite(value) or not 0 < value <= 1:
            raise ValueError('Threshold must be greater than 0 and at most 1.')
        with self.lock: self.threshold = value

    def snapshot(self, now=None):
        now = time.monotonic() if now is None else now
        with self.lock:
            elapsed = self.times[-1]-self.times[0] if len(self.times)>1 else 0
            fps = (len(self.times)-1)/elapsed if elapsed>0 else 0
            age = None if self.last_frame is None else max(0,now-self.last_frame)
            return dict(state=self.state, error=self.error, frames=self.frames, fps=fps,
                        age=age, resolution=self.resolution, processing_ms=self.processing_ms,
                        faces=[dict(face) for face in self.faces], debug=self.debug,
                        threshold=self.threshold, camera_index=self.camera_index,
                        tracking=self.tracking, tracking_ms=self.tracking_ms,
                        landmarks=self.landmarks, animation=self.animation,
                        tracking_error=self.tracking_error, hands_enabled=self.hands_enabled,
                        hands_state=self.hands_state, hands_ms=self.hands_ms,
                        hands=[dict(hand) for hand in self.hands], hands_error=self.hands_error,
                        apps_enabled=self.apps_enabled, app_action=self.app_action,
                        camera_paused=self.camera_paused, camera_light=dict(self.camera_light),
                        latency=dict(self.latency), counters=dict(self.counters), record_errors=dict(self.record_errors))
