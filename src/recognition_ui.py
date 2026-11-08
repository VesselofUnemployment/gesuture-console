"""Local camera preview and management terminal."""
import ctypes
import importlib
import queue
import sqlite3
import threading
import time
import tkinter as tk
import cv2
from admin_commands import SessionGate
from presence_writer import AsyncPresenceTracker
from admin_terminal import AdminTerminal
from camera_view import fitted_size, mirrored_results, registration_preview
from recapture_ui import RecaptureSession, RecaptureView
from vision_runtime import VisionDiagnostics
from profile_store import ProfileRepository
from vision_hud import BoxSmoother
from face_tracking import FaceTracker, mirror_points
from recognition_sequence import RecognitionSequence
from activity_feed import ActivityFeed
from soft_sounds import SoftSounds
from hand_tracking import HandTracker, draw_hands
from background_runtime import WakeGesture
from desktop_notice import DesktopNotice
from app_gestures import AppGesture
from app_launcher import AppLauncher
from camera_idle import CameraIdle
from recognition_presence import RecognitionPresence
from face_continuation import FaceContinuation
from desktop_control import DesktopControl, hand_input
from runtime_lifecycle import CameraLease, cleanup_steps, LOG
from latest_frames import LatestFrames
from hand_pipeline import HandPipeline
from gesture_permissions import can_track_hands, can_control_desktop, can_launch_gesture_apps


class RecognitionWindow:
    def __init__(self, root, detector=None, recognizer=None, camera_index=0, threshold=0.65, repo=None, start_camera=True,
                 background=False, resident=False, instance=None, tray_factory=None):
        self.root, self.threshold = root, threshold
        self.resident = resident or background
        self.instance = instance
        self.visible = threading.Event()
        if background: root.withdraw()
        else: self.visible.set()
        self.actions = queue.SimpleQueue()
        self.wake_gesture = WakeGesture()
        self.app_gesture = AppGesture()
        self.launcher = AppLauncher()
        self.control = DesktopControl(self.launcher)
        self.tray = None
        self.notice = None
        self.topmost_job = None
        self.repo = repo if repo is not None else ProfileRepository()
        self.people = self.repo.people()
        self.gate = SessionGate(5)
        self.authority = RecognitionPresence()
        self.presence = AsyncPresenceTracker(self.repo)
        self.feed = ActivityFeed()
        self.sounds = SoftSounds()
        self.previous_owner = None
        self.terminal = None
        self.recapture = None
        self.recapturing = threading.Event()
        self.capture_requests = queue.Queue(maxsize=1)
        self.capture_results = queue.SimpleQueue()
        self.diagnostics = VisionDiagnostics(camera_index, threshold)
        self.diagnostics.app_shortcuts = self.launcher.bindings
        self.stop = threading.Event()
        self.reload = threading.Event()
        self.events = queue.Queue(maxsize=1)
        self.hand_events = queue.Queue(maxsize=1)
        self.latest_draw_hands = ((), float("-inf"), -1)
        self.display = self.photo = None
        self.worker = None
        self.closed = False
        self.closing = False
        self.shutdown_errors = []
        self.shutdown_poll = None
        self.terminating = False
        self.termination_job = None
        self.revision = 0
        root.title('Vision Control')
        root.geometry('960x720')
        root.minsize(640, 480)
        root.configure(bg='#080d14')
        self.canvas = tk.Canvas(root, bg='#080d14', highlightthickness=0, width=1, height=1)
        self.canvas.pack(fill='both', expand=True)
        self.image_item = self.canvas.create_image(0, 0, anchor='center')
        self.debug_item = self.canvas.create_text(16,16,anchor='nw',text='',fill='#9fd7fa',font=('Consolas',10),justify='left')
        self.message_item = self.canvas.create_text(480, 360, text='Connecting camera...', fill='#90a3b9', font=('Consolas', 12))
        self.panel_button = tk.Button(self.canvas, text='ADMIN PANEL  >_', font=('Consolas', 11, 'bold'),
                                      bg='#142c42', fg='#83cfff', activebackground='#204664', activeforeground='white',
                                      relief='flat', borderwidth=0, padx=18, pady=10, cursor='hand2', command=self.open_panel)
        self.button_item = self.canvas.create_window(20, 20, anchor='sw', window=self.panel_button, state='hidden')
        self.control_button = tk.Button(self.canvas, text='CONTROL MODE / OFF', font=('Consolas', 11, 'bold'),
                                      bg='#142c42', fg='#83cfff', activebackground='#204664', activeforeground='white',
                                      relief='flat', borderwidth=0, padx=18, pady=10, cursor='hand2', command=self.toggle_control)
        self.control_item = self.canvas.create_window(940, 20, anchor='se', window=self.control_button, state='hidden')
        self.canvas.bind('<Configure>', lambda e: self.redraw())
        root.bind('<F11>', self.fullscreen)
        root.bind('<Escape>', self.escape)
        root.bind('<q>', self.quit_key)
        root.bind('<Q>', self.quit_key)
        root.bind('<k>', self.capture_key)
        root.bind('<K>', self.capture_key)
        root.protocol('WM_DELETE_WINDOW', self.request_close)
        if self.resident:
            try:
                if tray_factory is None:
                    from windows_tray import NativeTray
                    tray_factory = NativeTray
                self.tray = tray_factory(self.actions.put)
            except Exception as error:
                # Never leave an invisible process without its visible exit control.
                self.resident = False
                self.visible.set()
                root.deiconify()
                self.canvas.itemconfigure(self.message_item,text=f'Tray unavailable: {error}')
        if self.resident:self.notice=DesktopNotice(root)
        if start_camera:
            self.worker = threading.Thread(target=self.camera_worker, args=(detector, recognizer, camera_index), daemon=True)
            self.worker.start()
        self.poll_id = root.after(30, self.poll)

    def request_close(self):
        if self.terminating:self.close()
        elif self.resident:self.hide_window()
        else:self.close()

    def hide_window(self):
        if self.terminating:return
        if not self.resident or self.tray is None:return
        self.visible.clear()
        self.app_gesture.suppress()
        self.wake_gesture.suppress()
        if self.recapture:self.recapture.cancel()
        if self.terminal:self.terminal.close()
        self.gate.lock('Session locked: window hidden.')
        self.report_lock()
        self.sounds.stop()
        self.root.attributes('-fullscreen',False)
        self.root.withdraw()
        self.display = self.photo = None
        self.canvas.itemconfigure(self.image_item,image='')

    def show_window(self):
        if self.closed:return
        was_hidden=not self.visible.is_set()
        self.visible.set()
        self.root.deiconify()
        self.root.state('normal')
        self.root.lift()
        if was_hidden:
            self.root.attributes('-topmost',True)
            if self.topmost_job is not None:self.root.after_cancel(self.topmost_job)
            self.topmost_job=self.root.after(200,self.clear_topmost)
        self.root.focus_force()
        self.redraw()

    def clear_topmost(self):
        self.topmost_job=None
        if not self.closed:self.root.attributes('-topmost',False)

    def fullscreen(self, event=None):
        self.root.attributes('-fullscreen', not bool(self.root.attributes('-fullscreen')))
        self.root.after_idle(self.redraw)
        return 'break'

    def quit_key(self, event=None):
        # Keep Q from closing the app when the terminal has focus.
        if self.terminal is None and self.recapture is None:
            self.close()
            return "break"

    def capture_key(self, event=None):
        if self.recapture is not None and (event is None or event.widget is not self.recapture.code):
            self.recapture.capture()
            return "break"

    def escape(self, event=None):
        if self.control.enabled:
            self.control.set_enabled(False)
            self.app_gesture.suppress()
            self.update_button()
            return 'break'
        if self.recapture is not None:
            self.recapture.cancel()
            return "break"
        if self.terminal is not None:
            self.terminal.close()
            return "break"
        if self.root.attributes('-fullscreen'):
            self.root.attributes('-fullscreen', False)
            self.root.after_idle(self.redraw)
        else:
            self.request_close()
        return 'break'

    def publish(self, event):
        try: self.events.put_nowait(event)
        except queue.Full:
            try: self.events.get_nowait()
            except queue.Empty: pass
            self.events.put_nowait(event)

    def publish_hands(self, hands, shape, captured, generation, finished):
        self.latest_draw_hands = (tuple(hands), captured, generation)
        event=([dict(number=h.number,gesture=h.gesture,fingers=h.fingers,session_start=getattr(h,'session_start',False)) for h in hands],
               [hand_input(h,shape) for h in hands],captured,generation,finished)
        try:self.hand_events.put_nowait(event)
        except queue.Full:
            try:self.hand_events.get_nowait()
            except queue.Empty:pass
            self.hand_events.put_nowait(event)

    def camera_worker(self, detector, recognizer, camera_index):
        module = importlib.import_module('recognize')
        camera = lease = None
        smoother = BoxSmoother()
        sequence = RecognitionSequence()
        hand_presence = RecognitionPresence()
        continuation = FaceContinuation()
        idle = CameraIdle()
        tracker = None
        hand_tracker = None
        hand_pipeline = None
        frames = None
        hand_error = ''
        tracking_error = ""
        last_reload = float('-inf')
        generation = 0
        try:
            lease = CameraLease(camera_index, self.repo.db.parent)
            camera = cv2.VideoCapture(camera_index, cv2.CAP_DSHOW)
            if not camera.isOpened():
                camera.release()
                camera = cv2.VideoCapture(camera_index)
            if not camera.isOpened(): raise RuntimeError('Could not open the camera. Close other windows using it.')
            try:
                hand_tracker = HandTracker()
                hand_pipeline = HandPipeline(hand_tracker,self.publish_hands,self.diagnostics)
            except Exception as error:
                hand_error = str(error)
            camera.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            frames=LatestFrames(camera,self.stop,
                on_frame=(lambda frame,stamp:hand_pipeline.offer(frame,stamp,self.revision)) if hand_pipeline is not None else None)
            while not self.stop.is_set():
                now = time.monotonic()
                if self.reload.is_set() or now - last_reload >= 1:
                    # Clear the event before loading so changes received during reload stay pending.
                    self.reload.clear()
                    generation = self.revision
                    try:
                        people = self.repo.people()
                    except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
                        hand_presence.reset()
                        continuation.reset()
                        if hand_pipeline is not None:
                            hand_pipeline.configure(False, generation)
                        self.diagnostics.fail(error)
                        self.publish(('error', 'Profile storage unavailable: ' + str(error)))
                        last_reload = float('-inf')
                        self.stop.wait(.25)
                        continue
                    self.diagnostics.set_record_errors(self.repo.record_errors)
                    profiles = {p.identity: p for p in people}
                    last_reload = now
                started = time.monotonic()
                settings = self.diagnostics.snapshot()
                self.threshold = settings['threshold']
                ok, frame, captured = frames.read()
                if self.stop.is_set():break
                if not ok: raise RuntimeError('Could not read a camera frame.')
                self.diagnostics.set_counters(camera_reads=frames.version, face_frames_skipped=frames.skipped,
                                              hand_frames_skipped=hand_pipeline.skipped if hand_pipeline else 0)
                paused = idle.update(frame)
                frames.set_interval(idle.interval if paused else 0.)
                self.diagnostics.set_camera_idle(paused, idle.stats())
                hands_allowed=can_track_hands(hand_presence.current(time.monotonic(),profiles))
                if hand_pipeline is not None:
                    hand_pipeline.configure(not paused and settings['hands_enabled'] and
                                            not self.recapturing.is_set() and hands_allowed,generation)
                    if not paused and (not settings['hands_enabled'] or self.recapturing.is_set() or not hands_allowed):
                        state=('OFF' if not settings['hands_enabled'] else
                               'PAUSED' if self.recapturing.is_set() else 'RESTRICTED')
                        self.diagnostics.set_hands(state,0.,())
                if paused:
                    continuation.reset()
                    # No face/hand/mesh inference, overlay drawing, or old-frame reuse.
                    sequence.prepare([], profiles, frame.shape, time.monotonic())
                    self.diagnostics.set_hands('PAUSED', 0., ())
                    self.diagnostics.set_tracking('PAUSED', 0., 0, 'SUSPENDED')
                    self.diagnostics.record(frame.shape, [], (time.monotonic()-started)*1000, captured=captured)
                    try:session = self.capture_requests.get_nowait()
                    except queue.Empty:session = None
                    if session is not None:
                        self.capture_results.put((session,'Camera image is dark. Open the camera cover before capture.'))
                    display = cv2.flip(frame, 1) if self.visible.is_set() else None
                    self.publish(('frame', display, [], profiles, captured, generation,
                                  display if self.recapturing.is_set() else None,
                                  dict(identity=None, phase='SUSPENDED', fraction=0., hands=[], camera_paused=True)))
                    # Event.wait permits immediate shutdown instead of a blocking sleep.
                    self.stop.wait(max(0., idle.interval-(time.monotonic()-started)))
                    continue
                # Hand inference starts now and publishes directly, without
                # waiting behind face matching, mesh animation or camera drawing.
                if hand_pipeline is not None:hand_pipeline.offer(frame,captured,generation)
                face_started=time.monotonic()
                detector.setInputSize((frame.shape[1], frame.shape[0]))
                _, faces = detector.detect(frame)
                continuation.prepare(0 if faces is None else len(faces), captured, self.threshold, generation)
                if self.recapturing.is_set(): continuation.reset()
                results = []
                metrics = []
                if faces is not None:
                    for face in faces:
                        label, color, score = module.analyze_face(frame, face, recognizer, people, self.threshold, metrics,
                                                                 continuation if not self.recapturing.is_set() else None)
                        results.append((face, label, color, score))
                self.diagnostics.set_latency(face_ms=(time.monotonic()-face_started)*1000,
                    frame_wait_ms=(face_started-captured)*1000)
                # The hand worker uses its own face lease. It never reads Tk's
                # mutable authority state and does no inference for USER/unknown.
                person=profiles.get(results[0][1]) if len(results)==1 else None
                hand_presence.observe(person,captured,profiles,unsafe=len(results)>1)
                hands_allowed=can_track_hands(hand_presence.current(time.monotonic(),profiles))
                # A toggle may have changed while face inference was running.
                settings=self.diagnostics.snapshot()
                if hand_pipeline is not None:
                    hand_pipeline.configure(settings['hands_enabled'] and not self.recapturing.is_set()
                                            and hands_allowed,generation)
                    if settings['hands_enabled'] and not self.recapturing.is_set() and not hands_allowed:
                        self.diagnostics.set_hands('RESTRICTED',0.,())
                try:
                    session = self.capture_requests.get_nowait()
                except queue.Empty: session = None
                if session is not None:
                    try: message = session.capture(frame,faces,recognizer)
                    except Exception as error: message = str(error)
                    self.capture_results.put((session,message))
                # Actual face matches feed the separate 3-second authority lease.
                # The presentation sequence never grants permissions.
                displayed = smoother.update(mirrored_results(results, frame.shape[1]), frame.shape)
                overlay_now = time.monotonic()
                measure = sequence.prepare(displayed, profiles, frame.shape, overlay_now) if self.visible.is_set() else False
                points = None
                original = None
                mesh_ms = 0.
                mesh_state = 'UNAVAILABLE' if tracker is None else 'IDLE'
                if self.recapturing.is_set() or not self.visible.is_set():
                    sequence.reset()
                    measure = False
                if measure and tracker is None and not tracking_error:
                    try:
                        tracker = FaceTracker()
                    except Exception as error:
                        tracking_error = str(error)
                if measure and tracker is not None:
                    mesh_started = time.monotonic()
                    try:
                        original = tracker.measure(frame, results[0][0], overlay_now)
                        if original is not None:
                            points = mirror_points(original, frame.shape[1])
                        tracking_error = ''
                        mesh_state = 'TRACKED' if points is not None else 'NOT_FOUND'
                    except Exception as error:
                        tracking_error = str(error)
                        mesh_state = 'ERROR'
                    mesh_ms = (time.monotonic()-mesh_started)*1000
                display = sequence.render(cv2.flip(frame, 1), displayed, profiles, points,
                                          overlay_now, tracking_error) if self.visible.is_set() else None
                hands = hand_pipeline.snapshot() if hand_pipeline is not None else []
                if not settings["hands_enabled"] or self.recapturing.is_set() or not hands_allowed:
                    hands=[]
                if hand_pipeline is None:
                    self.diagnostics.set_hands("UNAVAILABLE",0.,(),hand_error)
                self.diagnostics.set_tracking(mesh_state, mesh_ms, 478 if points is not None else 0,
                                              sequence.phase, tracking_error)
                self.diagnostics.record(frame.shape,metrics,(time.monotonic()-started)*1000, captured=captured)
                preview = registration_preview(frame,faces) if self.recapturing.is_set() else None
                presentation=dict(identity=results[0][1] if len(results)==1 and results[0][1] in profiles else None,
                                  phase=sequence.phase,fraction=sequence.state(overlay_now)['fraction'],
                                  hands=[dict(number=h.number,gesture=h.gesture,fingers=h.fingers) for h in hands],
                                  control_hands=[], async_hands=True)
                self.publish(('frame', display, [r[1] for r in results], profiles, captured, generation,
                              preview, presentation))
                # In desktop control, match authority at 10Hz while the camera
                # reader feeds hands at full rate. Avoid competing face work on
                # every hand frame; identity/role checks still run on every result.
                if self.control.enabled and not self.visible.is_set():
                    self.stop.wait(max(0.,.1-(time.monotonic()-started)))
        except Exception as error:
            self.diagnostics.fail(error)
            self.publish(('error', str(error)))
        finally:
            try:
                if frames is not None:
                    while not frames.close(timeout=.1):
                        self.diagnostics.fail('Waiting for camera driver to finish reading; camera has not been released.')
                if tracker is not None:
                    tracker.close()
            finally:
                try:
                    if hand_pipeline is not None:hand_pipeline.close()
                    elif hand_tracker is not None:hand_tracker.close()
                finally:
                    try:
                        if camera is not None:
                            camera.release()
                    finally:
                        if lease is not None:
                            lease.close()

    def accept_frame(self, display, identities, profiles, timestamp, preview=None, presentation=None):
        self.current_profiles=profiles
        self.display = display
        if self.recapture is not None:
            self.recapture.show_frame(preview if preview is not None else display)
        self.canvas.itemconfigure(self.message_item, text='')
        recognized = [identity for identity in identities if identity in profiles and profiles[identity].active]
        self.feed.observe(identities,profiles,timestamp)
        self.presence.observe(recognized, timestamp)
        person = profiles[recognized[0]] if len(identities) == 1 and len(recognized) == 1 else None
        authority = self.authority.observe(person, timestamp, profiles, unsafe=len(identities)>1)
        # Expired/stalled worker frames cannot extend a remembered identity.
        authority = self.authority.current(time.monotonic(), profiles)
        if self.notice:
            self.notice.observe(person,timestamp,enabled=not self.recapturing.is_set() and len(identities)<=1)
        self.gate.observe(person, timestamp, unsafe=len(identities) > 1)
        self.report_lock()
        if self.terminal and self.gate.owner is None: self.terminal.set_locked()
        self.diagnostics.set_latency(face_ui_ms=(time.monotonic()-timestamp)*1000)
        if presentation is not None:
            if not presentation.get('async_hands',False):
                self.accept_hands(presentation.get('hands',()),presentation.get('control_hands',()),timestamp,
                                  camera_paused=presentation.get('camera_paused',False))
            if self.visible.is_set():
                self.sounds.observe(presentation['identity'],presentation['phase'],presentation['fraction'],timestamp)
        self.update_button()
        self.redraw()

    def accept_hands(self, hands, controls, timestamp, camera_paused=False, finished=None):
        now=time.monotonic()
        authority=self.authority.current(now,self.people_by_identity())
        settings=self.diagnostics.snapshot()
        fresh=(can_track_hands(authority) and settings['hands_enabled'] and not settings['camera_paused'] and not camera_paused
               and self.terminal is None and not self.recapturing.is_set()
               and 0 <= now-timestamp <= .6)
        started=time.monotonic()
        self.control.update(controls,timestamp,
            authorized=can_control_desktop(authority),fresh=fresh)
        applied=time.monotonic()
        self.diagnostics.set_latency(control_ms=(applied-started)*1000,
            hand_ui_ms=(now-(finished if finished is not None else timestamp))*1000,
            read_to_control_ms=(applied-timestamp)*1000)
        command=self.app_gesture.update(hands,
            authority.identity if can_launch_gesture_apps(authority) else None,timestamp,
            enabled=self.resident and not self.control.enabled and settings['apps_enabled'] and fresh)
        if command is not None:self.launcher.request(command)
        if self.wake_gesture.update(hands,authority.identity if can_track_hands(authority) else None,timestamp,
                                    enabled=self.resident and fresh):
            self.show_window()
        if self.visible.is_set():
            self.redraw()

    def update_button(self):
        show = not self.terminating and self.gate.current_candidate() is not None
        self.canvas.itemconfigure(self.button_item, state='normal' if show else 'hidden')
        person=self.authority.current(time.monotonic(),self.people_by_identity())
        allowed=can_control_desktop(person)
        self.canvas.itemconfigure(self.control_item,state='normal' if not self.terminating and (allowed or self.control.enabled) else 'hidden')
        self.control_button.configure(text='CONTROL MODE / '+('ON' if self.control.enabled else 'OFF'))

    def people_by_identity(self):
        # Profiles are refreshed by the worker; current authority still checks revisions.
        return getattr(self,'current_profiles',{p.identity:p for p in self.people})

    def toggle_control(self):
        if self.control.enabled:
            self.control.set_enabled(False)
            self.app_gesture.suppress()
        else:
            person=self.authority.current(time.monotonic(),self.people_by_identity())
            if not can_control_desktop(person) or self.terminal is not None or self.recapturing.is_set():
                if self.notice:self.notice.show_message('CONTROL MODE unavailable','Recognise a SUPERVISOR or ADMIN and close the terminal')
                return
            if not self.diagnostics.snapshot()['hands_enabled']:
                if self.notice:self.notice.show_message('CONTROL MODE unavailable','Enable hand tracking first')
                return
            self.app_gesture.suppress()
            if self.control.set_enabled(True):
                # Make the desktop reachable. Hand recognition continues while hidden.
                if self.resident:self.hide_window()
                if self.notice:self.notice.show_message('CONTROL MODE active','Point / pinch to click  ·  Open palm / wheel  ·  Esc / exit')
            elif self.notice:self.notice.show_message('CONTROL MODE unavailable',self.control.error)
        self.update_button()

    def poll(self):
        if self.closed or self.closing:
            return
        try:
            self._poll_once()
        except (OSError, ValueError, RuntimeError, sqlite3.Error, tk.TclError, cv2.error) as error:
            LOG.exception('Main loop failed')
            self.diagnostics.fail(error)
            self.authority.reset()
            self.gate.candidate = None
            self.gate.lock('Session locked: runtime error.')
            self.app_gesture.suppress()
            cleanup_steps([('disable control', lambda: self.control.set_enabled(False)),
                           ('hide notice', lambda: self.notice.hide() if self.notice else None),
                           ('lock terminal', lambda: self.terminal.set_locked() if self.terminal else None)])
            self.feed.fail(str(error))
        finally:
            if not self.closed and not self.closing:
                delay = 100 if self.diagnostics.snapshot()['camera_paused'] else (12 if self.control.enabled else 30)
                self.poll_id = self.root.after(delay, self.poll)

    def _poll_once(self):
        if self.terminating:
            # Worker shutdown/terminal animation are driven without granting new actions.
            while not self.actions.empty():
                if self.actions.get_nowait() == 'quit':
                    self.close()
                    return
            return
        while not self.actions.empty():
            action=self.actions.get_nowait()
            if action=='show':self.show_window()
            elif action=='hide':self.hide_window()
            elif action=='face-diagnostics':self.diagnostics.export_face_summary()
            elif action=='apps-toggle':
                self.diagnostics.set_apps_enabled(not self.diagnostics.snapshot()['apps_enabled'])
                self.app_gesture.suppress()
            elif action=='control-toggle':self.toggle_control()
            elif action=='quit':
                self.close()
                return
        if self.instance is not None and self.instance.take_activation():self.show_window()
        if self.tray:
            self.tray.apps_enabled=self.diagnostics.snapshot()['apps_enabled']
            self.tray.control_enabled=self.control.enabled
        was_control=self.control.enabled
        self.control.poll(time.monotonic())
        if was_control and not self.control.enabled:self.app_gesture.suppress()
        while not self.launcher.results.empty():
            command,ok,error=self.launcher.results.get_nowait()
            name=self.launcher.names[command]
            if command=='session':
                self.diagnostics.set_app_action(error)
                self.feed.add('SESSION START / '+error)
                if self.notice:self.notice.show_message('SESSION START' if ok else 'SESSION START / PARTIAL',
                    error.splitlines()[0] if error else 'Launch requested')
                continue
            message=f'Open requested: {name}' if ok else f'{name}: {error}'
            self.diagnostics.set_app_action(message)
            self.feed.add(message)
            if self.notice:self.notice.show_message('Opening '+name if ok else 'Could not open '+name,
                'Gesture '+('Fist' if command=='fist' else command) if ok else 'Check vision status')
        while not self.capture_results.empty():
            session,message = self.capture_results.get_nowait()
            if self.recapture is not None and self.recapture.session is session:
                self.recapture.captured(message)
        self.presence.expire(time.monotonic())
        try:
            event = self.events.get_nowait()
            if event[0] == 'frame':
                _, display, identities, profiles, timestamp, generation = event[:6]
                preview = event[6] if len(event)>6 else None
                presentation = event[7] if len(event)>7 else None
                if self.reload.is_set() or generation != self.revision:
                    # Discard frames prepared before the command changed.
                    self.gate.observe(None)
                    self.gate.candidate = None
                    self.authority.reset()
                    self.app_gesture.suppress()
                    self.control.set_enabled(False)
                    if self.notice:self.notice.observe(None,time.monotonic(),enabled=False)
                else:
                    self.accept_frame(display, identities, profiles, timestamp, preview, presentation)
            else:
                self.display = None
                self.authority.reset()
                self.gate.candidate = None
                self.app_gesture.suppress()
                self.control.set_enabled(False)
                if self.notice:self.notice.observe(None,time.monotonic(),enabled=False)
                if self.tray:self.tray.set_tip('Vision Control / Camera unavailable - click to inspect')
                self.canvas.itemconfigure(self.image_item, image='')
                self.canvas.itemconfigure(self.message_item, text=event[1])
                self.gate.observe(None)
                self.gate.lock('Session locked: camera unavailable.')
                self.feed.fail(event[1])
                self.report_lock()
                if self.terminal: self.terminal.set_locked()
                if self.recapture:
                    self.recapture.status.configure(text=event[1])
                    self.recapture.capture_button.configure(state='disabled')
                    self.recapture.last_frame=None
        except queue.Empty: pass
        # Face decisions and hand samples have separate newest-only mailboxes.
        # Always apply a newly available face decision before the hand command.
        try:
            hands,controls,stamp,generation,finished=self.hand_events.get_nowait()
            if not self.reload.is_set() and generation==self.revision:
                self.accept_hands(hands,controls,stamp,finished=finished)
        except queue.Empty:pass
        authority=self.authority.current(time.monotonic(),self.people_by_identity())
        if self.control.enabled and not can_control_desktop(authority):
            self.control.set_enabled(False);self.app_gesture.suppress()
        self.gate.valid(self.gate.owner)
        self.report_lock()
        self.update_button()

    def redraw(self):
        if not self.visible.is_set():return
        width, height = self.canvas.winfo_width(), self.canvas.winfo_height()
        self.canvas.coords(self.message_item, width / 2, height / 2)
        self.canvas.coords(self.button_item, 20, height - 20)
        self.canvas.coords(self.control_item,width-20,height-20)
        if self.display is None or width < 2 or height < 2: return
        if self.terminal is None and self.recapture is None:
            image = self.display.copy()
            hands, captured, generation = self.latest_draw_hands
            now = time.monotonic()
            authority = self.authority.current(now, self.people_by_identity())
            settings = self.diagnostics.snapshot()
            if (generation == self.revision and not self.reload.is_set()
                    and settings['hands_enabled'] and not settings['camera_paused']
                    and can_track_hands(authority) and 0 <= now-captured <= .22):
                draw_hands(image, hands)
            h, w = image.shape[:2]
            vw, vh = fitted_size(image.shape, width, height)
            resized = cv2.resize(image, (vw, vh), interpolation=cv2.INTER_AREA if vw < w else cv2.INTER_LINEAR)
            rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
            self.photo = tk.PhotoImage(data=f'P6\n{vw} {vh}\n255\n'.encode('ascii') + rgb.tobytes(), format='PPM')
            self.canvas.itemconfigure(self.image_item, image=self.photo)
            self.canvas.coords(self.image_item, width / 2, height / 2)
            self.canvas.tag_raise(self.button_item)
            self.canvas.tag_raise(self.control_item)
            self.canvas.tag_raise(self.message_item)
        state = self.diagnostics.snapshot()
        self.canvas.itemconfigure(self.message_item,
            text='CAMERA COVERED / LOW POWER' if state['camera_paused'] else '')
        if state['debug']:
            lines=[f"FPS {state['fps']:.1f} | {state['processing_ms']:.1f}ms | THRESHOLD {state['threshold']:.3f}"]
            lines.append('CAMERA POWER '+('LOW / DARK IMAGE' if state['camera_paused'] else 'NORMAL'))
            lines.append(f"MESH {state['tracking']} | {state['tracking_ms']:.1f}ms | {state['landmarks']} POINTS | {state['animation']}")
            lines.append(f"HANDS {state['hands_state']} | {state['hands_ms']:.1f}ms | {len(state['hands'])} HANDS")
            for hand in state['hands']:
                count='--' if hand['fingers'] is None else str(hand['fingers'])
                lines.append(f"HAND {hand['number']:02d} | NUMBER {count} | {hand['gesture']}")
            lines.append('APP CONTROL '+('ON' if state['apps_enabled'] else 'OFF'))
            lines.append('DESKTOP CONTROL '+('ON' if self.control.enabled else 'OFF'))
            latency=state['latency']
            lines.append(f"CONTROL READ→APPLY {latency.get('read_to_control_ms',0.):.1f}ms | UI {latency.get('hand_ui_ms',0.):.1f}ms")
            for face in state['faces']:
                score=face.get('score')
                blur,light=face.get('blur'),face.get('brightness')
                blur_text='--' if blur is None else f'{blur:.1f}'
                light_text='--' if light is None else f'{light:.1f}'
                lines.append(f"{face['reason']} | SCORE {'--' if score is None else f'{score:.3f}'} | BLUR {blur_text} | LIGHT {light_text}")
            self.canvas.itemconfigure(self.debug_item,text='\n'.join(lines))
            self.canvas.tag_raise(self.debug_item)
        else: self.canvas.itemconfigure(self.debug_item,text='')

    def changed(self):
        self.revision += 1
        self.authority.reset()
        self.app_gesture.suppress()
        self.control.set_enabled(False)
        if self.notice:self.notice.observe(None,time.monotonic(),enabled=False)
        self.reload.set()
        self.gate.candidate = None
        self.update_button()

    def terminal_closed(self):
        self.terminal = None
        self.report_lock()
        if not self.closed:
            self.canvas.focus_set()
            self.update_button()

    def report_lock(self):
        if self.previous_owner is not None and self.gate.owner is None:
            self.feed.add(self.gate.reason or 'Management panel closed')
        self.previous_owner=self.gate.owner

    def open_panel(self):
        self.control.set_enabled(False)
        try:
            owner = self.gate.open()
            actor = self.repo.get(owner)
            if not actor or not actor.active or actor.role not in ('ADMIN', 'SUPERVISOR'):
                self.gate.lock(); return
            if self.terminal and not self.terminal.locked and self.terminal.actor_identity == owner:
                self.terminal.window.lift(); self.terminal.entry.focus_set(); return
            if self.terminal:
                self.terminal.close()
                # Reopen for the new owner after close() locks the previous session.
                self.gate.open()
            self.terminal = AdminTerminal(self.root, self.repo, self.gate, owner, self.changed,
                                          self.terminal_closed, self.threshold, self.diagnostics,
                                          self.presence, self.start_recapture, self.feed, self.sounds,
                                          on_terminate=self.terminate_connection)
            self.previous_owner=owner
            self.feed.add(f'Panel opened · {actor.name} · button')
            self.sounds.play('panel')
        except (ValueError, OSError, sqlite3.Error) as error:
            self.canvas.itemconfigure(self.message_item, text=str(error))

    def start_recapture(self, plan):
        if not self.terminal or not self.gate.valid(self.terminal.actor_identity):
            raise ValueError('Session locked. Reopen the panel before recapture.')
        session=RecaptureSession(self.repo,plan,self.terminal.actor_identity)
        self.terminal.window.place_forget()
        self.recapture=RecaptureView(self.root,session,self.gate,self.request_capture,self.recapture_done)
        self.recapturing.set()

    def request_capture(self, session):
        if self.recapture is None or self.recapture.session is not session:
            raise ValueError('Recapture is no longer active.')
        try:self.capture_requests.put_nowait(session)
        except queue.Full:raise ValueError('A capture is already pending.') from None

    def recapture_done(self, saved, message):
        self.recapture=None
        self.recapturing.clear()
        if saved:self.changed()
        terminal=self.terminal
        if terminal is None or self.closed:return
        if self.gate.valid(terminal.actor_identity):
            terminal.locked=False
            terminal.entry.configure(state='normal',show='')
            terminal.refresh_identity()
            terminal.window.place(relx=.03,rely=.04,relwidth=.94,relheight=.92)
            terminal.window.lift()
            terminal.write(message+'\n\n','success' if saved else 'dim')
            terminal.entry.focus_set()
        else:
            terminal.close()
            self.canvas.itemconfigure(self.message_item,text=message)

    def terminate_connection(self):
        if self.closed or self.terminating:return
        if self.terminal is None:raise ValueError('Open an ADMIN terminal to terminate the connection.')
        actor = self.terminal.engine.actor()
        if actor.role != 'ADMIN':raise ValueError('Only ADMIN may terminate the connection.')
        self.terminating = True
        self.control.set_enabled(False)
        self.terminal.begin_shutdown()
        self.authority.reset()
        self.gate.candidate = None
        self.gate.lock('Connection terminated.')
        self.app_gesture.suppress()
        self.wake_gesture.suppress()
        self.launcher.close()
        self.sounds.stop()
        if self.notice:self.notice.hide()
        self.stop.set()
        self.update_button()
        self.termination_started = time.monotonic()
        self.termination_stage = 0
        self.termination_job = self.root.after(50,self.shutdown_step)

    def shutdown_step(self):
        self.termination_job = None
        if self.closed:return
        # Camera is released in the worker's finally block before declaring success.
        if self.worker is not None and self.worker.is_alive():
            self.termination_job = self.root.after(50,self.shutdown_step)
        elif self.termination_stage == 0:
            if time.monotonic()-self.termination_started < .35:
                self.termination_job = self.root.after(50,self.shutdown_step)
                return
            if self.terminal:self.terminal.write('Authority offline.\n','dim')
            self.termination_stage = 1
            self.termination_job = self.root.after(350,self.shutdown_step)
        elif self.termination_stage == 1:
            if self.terminal:self.terminal.write('Connection terminated.\n','blue')
            self.termination_stage = 2
            self.termination_job = self.root.after(450,self.close)

    def close(self):
        if self.closed:
            return
        if not self.closing:
            self.closing = True
            self.stop.set()
            self.authority.reset()
            self.gate.lock('Connection closing.')
            self.gate.candidate = None
            steps = []
            for name in ('poll_id', 'termination_job', 'topmost_job'):
                job = getattr(self, name, None)
                if job is not None:
                    steps.append((name, lambda job=job: self.root.after_cancel(job)))
                    setattr(self, name, None)
            for name in ('control', 'launcher', 'sounds', 'notice', 'tray'):
                resource = getattr(self, name, None)
                if resource is not None:
                    action = resource.stop if name == 'sounds' else resource.close
                    steps.append((name, action))
            if self.recapture:
                steps.append(('recapture', self.recapture.cancel))
            if self.terminal:
                steps.append(('terminal', self.terminal.close))
            self.shutdown_errors.extend(cleanup_steps(steps))
        self.shutdown_poll = None
        # Fast releases finish immediately; a slow/stalled driver keeps its
        # exclusive lease and the Tk loop remains responsive until it finishes.
        if self.worker and self.worker.is_alive():
            self.worker.join(timeout=.02)
            if self.worker.is_alive():
                self.canvas.itemconfigure(self.message_item, text='Stopping / waiting for camera driver...')
                self.shutdown_poll = self.root.after(50, self.close)
                return
        try:
            if self.presence.close() is False:
                self.shutdown_poll = self.root.after(50, self.close)
                return
        except Exception as error:
            self.shutdown_errors.append(('login sessions', type(error).__name__))
            LOG.exception('Login session cleanup failed')
        self.closed = True
        self.root.destroy()


def run_recognition(camera_index, threshold, detector, recognizer, background=False, instance=None):
    try: ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError): pass
    root = tk.Tk()
    if background:root.withdraw()
    app = RecognitionWindow(root, detector, recognizer, camera_index, threshold,
                            background=background,resident=True,instance=instance)
    try:root.mainloop()
    finally:app.close()
