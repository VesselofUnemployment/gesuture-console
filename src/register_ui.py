"""Retro registration wizard for a name, fields, role, and three face samples."""
import argparse
import importlib
from person_store import ACCESS_FILE
import queue
import sqlite3
import threading
import tkinter as tk

import cv2
import numpy as np
from runtime_lifecycle import CameraLease

from camera_view import fitted_size, registration_preview

from person_store import (clean_name, clean_fields, ensure_new_person, load_people,
                          role_for_code, save_person, FIELDS)

enrollment = importlib.import_module("face_enrollment")
BG, PANEL, INPUT = "#0d1823", "#1c2d3d", "#101e2b"
EDGE, TEXT, MUTED, ACCENT = "#49677e", "#d4e0e8", "#8fa7ba", "#76c4ee"
POSES = ("FRONT", "SLIGHTLY RIGHT", "SLIGHTLY LEFT")
PROMPTS = ("Look straight at the camera.", "Turn your head slightly to your right.",
           "Turn your head slightly to your left.")


class CaptureSession:
    def __init__(self, name, fields, role, directory=None, legacy=None):
        self.name, self.fields, self.role = clean_name(name), clean_fields(fields), role
        self.samples = []
        self.path = None
        self.directory, self.legacy = directory, legacy
        self.people = load_people(**({"directory": directory} if directory is not None else {}), legacy=legacy)
        ensure_new_person(self.name, None, self.people)

    def capture(self, frame, faces, recognizer):
        if self.path is not None:
            return "Registration complete."
        face, reason = enrollment.select_face(faces, frame.shape)
        if face is None:
            if faces is None or not len(faces):
                return "No face found. Look at the camera and try again."
            if len(faces) != 1:
                return "Only one person may be visible during registration."
            return "Move closer and keep your whole face in view."
        aligned = recognizer.alignCrop(frame, face)
        gray = cv2.cvtColor(aligned, cv2.COLOR_BGR2GRAY)
        if cv2.Laplacian(gray, cv2.CV_64F).var() < 25:
            return "Image is blurry. Hold still and try again."
        if not 35 <= gray.mean() <= 220:
            return "Adjust the lighting and try again."
        feature = enrollment.normalize(recognizer.feature(aligned))
        ensure_new_person(self.name, [feature], self.people)
        if self.samples and float(np.dot(self.samples[0], feature)) < enrollment.CONSISTENCY_THRESHOLD:
            return "Face does not match the first sample. Face forward and try again."
        if len(self.samples) == 2:
            options = {} if self.directory is None else {"directory": self.directory}
            self.path = save_person(self.name, self.fields, self.role,
                                    self.samples + [feature], legacy=self.legacy, **options)
        self.samples.append(feature)
        return "Registration complete." if self.path else "Sample captured. Follow the next pose."


class CameraWorker(threading.Thread):
    def __init__(self, camera_index, session):
        super().__init__(daemon=True)
        self.camera_index, self.session = camera_index, session
        self.events = queue.Queue(maxsize=1)
        self.stop_event, self.capture_event = threading.Event(), threading.Event()
        self.requests = 0
        self.processed = 0

    def publish(self, event):
        try:
            self.events.get_nowait()
        except queue.Empty:
            pass
        try:
            self.events.put_nowait(event)
        except queue.Full:
            pass

    def run(self):
        camera = lease = None
        try:
            lease = CameraLease(self.camera_index)
            detector, recognizer = enrollment.load_models()
            if self.stop_event.is_set():
                return
            camera = cv2.VideoCapture(self.camera_index, cv2.CAP_DSHOW)
            if not camera.isOpened():
                camera.release()
                camera = cv2.VideoCapture(self.camera_index)
            if not camera.isOpened():
                raise RuntimeError("Camera unavailable. Close other camera apps and try again.")
            camera.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            message = "When ready, press CAPTURE or K."
            while not self.stop_event.is_set():
                ok, frame = camera.read()
                if not ok:
                    raise RuntimeError("Could not read the camera.")
                detector.setInputSize((frame.shape[1], frame.shape[0]))
                _, faces = detector.detect(frame)
                if self.stop_event.is_set():
                    break
                pending = self.capture_event.is_set()
                if pending:
                    self.capture_event.clear()
                    try:
                        message = self.session.capture(frame, faces, recognizer)
                    except (ValueError, RuntimeError) as error:
                        message = str(error)
                    self.processed = self.requests
                display = registration_preview(frame, faces)
                self.publish({"frame": display, "count": len(self.session.samples),
                              "message": message, "done": self.session.path is not None,
                              "processed": self.processed})
                if self.session.path is not None:
                    break
        except (OSError, ValueError, RuntimeError, KeyError, cv2.error, sqlite3.Error) as error:
            self.publish({"error": str(error)})
        finally:
            try:
                if camera is not None:
                    camera.release()
            finally:
                if lease is not None:
                    lease.close()


class RegistrationWindow:
    def __init__(self, root, camera_index=0, preview=False):
        self.root, self.camera_index, self.preview = root, camera_index, preview
        self.step, self.worker, self.job = 0, None, None
        self.completed, self.busy, self.ready = False, False, False
        self.pending_request = 0
        self.name = tk.StringVar(root)
        self.code = tk.StringVar(root)
        self.field_vars = {field: tk.BooleanVar(root) for field in FIELDS}
        self.role = "USER"
        self.status = tk.StringVar(root)
        self.pose = tk.StringVar(root)
        self.progress = tk.StringVar(root)
        self.photo = None
        self.last_frame = None
        self.camera_view = None
        self.camera_image = None
        self.resize_job = None
        root.title("Person Registration")
        root.geometry("800x680")
        root.minsize(780, 660)
        root.configure(bg=BG)
        root.option_add("*Font", "Tahoma 11")
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.bind("<Return>", lambda _event: self.next())
        root.bind("<Escape>", lambda _event: self.close())
        root.bind("<KeyPress-k>", lambda _event: self.capture() if self.step == 3 else None)
        root.bind("<KeyPress-K>", lambda _event: self.capture() if self.step == 3 else None)
        outer = tk.Frame(root, bg=BG)
        outer.pack(fill="both", expand=True, padx=22, pady=20)
        top = tk.Frame(outer, bg=PANEL, bd=2, relief="raised")
        top.pack(fill="x")
        tk.Label(top, text="PERSON REGISTRATION", bg=PANEL, fg=TEXT,
                 font=("Courier New", 17, "bold"), anchor="w").pack(fill="x", padx=18, pady=13)
        self.steps_label = tk.Label(outer, bg=BG, fg=MUTED, font=("Courier New", 10), anchor="w")
        self.steps_label.pack(fill="x", pady=(17, 12))
        self.body = tk.Frame(outer, bg=PANEL, highlightbackground=EDGE, highlightthickness=1)
        self.body.pack(fill="both", expand=True)
        tk.Label(outer, textvariable=self.status, bg=BG, fg=ACCENT, anchor="w",
                 justify="left", wraplength=740, height=2).pack(fill="x", pady=(10, 6))
        footer = tk.Frame(outer, bg=BG)
        footer.pack(fill="x")
        self.cancel_button = self.button(footer, "CANCEL", self.close)
        self.cancel_button.pack(side="left")
        self.next_button = self.button(footer, "NEXT >", self.next, primary=True)
        self.next_button.pack(side="right")
        self.back_button = self.button(footer, "< BACK", self.back)
        self.back_button.pack(side="right", padx=10)
        self.show_step()

    def button(self, parent, text, command, primary=False):
        return tk.Button(parent, text=text, command=command, bg="#294d68" if primary else "#24394c",
                         fg=TEXT, activebackground="#3c6380", activeforeground="white",
                         disabledforeground="#65798c", relief="raised", bd=2, padx=18, pady=7,
                         font=("Tahoma", 10, "bold"), cursor="hand2", highlightthickness=0)

    def label(self, text, parent=None, large=False, color=TEXT, **options):
        item = tk.Label(parent or self.body, text=text, bg=PANEL, fg=color, anchor="w",
                        font=("Courier New", 19, "bold") if large else ("Tahoma", 11), **options)
        return item

    def show_step(self):
        self.cancel_camera_redraw()
        self.last_frame = None
        self.camera_view = self.camera_image = None
        self.photo = None
        for child in self.body.winfo_children():
            child.destroy()
        self.status.set("")
        titles = ("NAME", "FIELD", "ACCESS LEVEL", "FACE REGISTRATION")
        self.steps_label.configure(text=" / ".join(f"{'>' if i == self.step else ' '} {i+1:02} {title}"
                                                   for i, title in enumerate(titles)))
        self.back_button.configure(state="disabled" if self.step == 0 else "normal")
        self.next_button.configure(text="NEXT >", state="normal")
        self.label(titles[self.step], large=True).pack(fill="x", padx=26, pady=(23, 10))
        if self.step == 0:
            self.label("Enter the name you want to appear beside your face.", color=MUTED).pack(fill="x", padx=26)
            self.label("DISPLAY NAME").pack(fill="x", padx=26, pady=(40, 9))
            entry = tk.Entry(self.body, textvariable=self.name, bg=INPUT, fg=TEXT, insertbackground=ACCENT,
                             relief="sunken", bd=2, font=("Tahoma", 16))
            entry.pack(fill="x", padx=26, ipady=9)
            self.label("1-24 characters. No spaces; underscores are allowed.", color=MUTED).pack(fill="x", padx=26, pady=12)
            entry.focus_set()
        elif self.step == 1:
            self.label("Select your field. You can choose more than one.", color=MUTED).pack(fill="x", padx=26)
            choices = tk.Frame(self.body, bg=PANEL)
            choices.pack(fill="x", padx=26, pady=24)
            for i, field in enumerate(FIELDS):
                check = tk.Checkbutton(choices, text=field, variable=self.field_vars[field],
                                       command=lambda f=field: self.field_changed(f), bg=PANEL, fg=TEXT,
                                       selectcolor=INPUT, activebackground="#29445c", activeforeground="white",
                                       anchor="w", bd=1, relief="flat", padx=10, pady=12, cursor="hand2")
                check.grid(row=i//2, column=i%2, sticky="ew", padx=(0, 12), pady=4)
            choices.columnconfigure(0, weight=1)
            choices.columnconfigure(1, weight=1)
        elif self.step == 2:
            self.label("Enter an access code, or leave empty for USER.", color=MUTED).pack(fill="x", padx=26)
            self.label("ACCESS CODE").pack(fill="x", padx=26, pady=(35, 9))
            entry = tk.Entry(self.body, textvariable=self.code, show="*", bg=INPUT, fg=TEXT,
                             insertbackground=ACCENT, relief="sunken", bd=2, font=("Tahoma", 16))
            entry.pack(fill="x", padx=26, ipady=9)
            self.label("USER  /  OPERATOR  /  SUPERVISOR  /  ADMIN", color=MUTED).pack(fill="x", padx=26, pady=18)
            entry.focus_set()
        else:
            self.label(f"{self.name.get()}   /   {self.role}", color=ACCENT).pack(fill="x", padx=26)
            line = tk.Frame(self.body, bg=PANEL)
            line.pack(fill="x", padx=26, pady=(8, 5))
            tk.Label(line, textvariable=self.pose, bg=PANEL, fg=TEXT, anchor="w",
                     font=("Courier New", 12, "bold"), wraplength=570, justify="left").pack(side="left")
            tk.Label(line, textvariable=self.progress, bg=PANEL, fg=MUTED).pack(side="right")
            # Canvas'in boyut istegi fotograf boyutundan etkilenmez; buyutmede dongu olusmaz.
            self.camera_view = tk.Canvas(self.body, width=1, height=1, bg=INPUT,
                                         highlightthickness=1, highlightbackground=EDGE)
            self.camera_view.pack(fill="both", expand=True, padx=26, pady=(2, 12))
            self.camera_placeholder = self.camera_view.create_text(20, 20, text="Opening camera...",
                                                                   fill=MUTED, anchor="nw")
            self.camera_view.bind("<Configure>", self.queue_camera_redraw)
            self.next_button.configure(text="CAPTURE", state="disabled")
            self.pose.set(POSES[0])
            self.progress.set("0 / 3")
            self.start_camera()

    def field_changed(self, field):
        if field == "Unspecified" and self.field_vars[field].get():
            for other in FIELDS[:-1]:
                self.field_vars[other].set(False)
        elif field != "Unspecified" and self.field_vars[field].get():
            self.field_vars["Unspecified"].set(False)

    def selected_fields(self):
        return clean_fields([field for field, var in self.field_vars.items() if var.get()])

    def next(self):
        if self.step == 3:
            if self.completed:
                self.close()
            else:
                self.capture()
            return
        try:
            if self.step == 0:
                self.name.set(clean_name(self.name.get()))
                ensure_new_person(self.name.get(), None, load_people())
            elif self.step == 1:
                self.selected_fields()
            elif self.step == 2:
                try:
                    self.role = role_for_code(self.code.get())
                except FileNotFoundError:
                    raise ValueError("Set up access passwords in settings.cmd first.") from None
                self.code.set("")
            self.step += 1
            self.show_step()
        except (ValueError, RuntimeError, OSError, KeyError, sqlite3.Error) as error:
            self.status.set(str(error))

    def stop_camera(self):
        self.cancel_camera_redraw()
        if self.job is not None:
            self.root.after_cancel(self.job)
            self.job = None
        if self.worker is not None:
            self.worker.stop_event.set()
            # Release the camera before starting a new registration session.
            self.worker.join(timeout=2)
            if self.worker.is_alive():
                raise RuntimeError("Camera is still stopping. Please wait and try again.")
            self.worker = None
        self.ready = self.busy = False

    def back(self):
        if self.completed:
            return
        try:
            self.stop_camera()
        except RuntimeError as error:
            self.status.set(str(error))
            return
        if self.step > 0:
            self.step -= 1
            self.role = "USER"
            self.code.set("")
            self.show_step()

    def start_camera(self):
        self.completed = False
        self.pending_request = 0
        if self.preview:
            from vision_hud import demo_frame
            self.show_frame(registration_preview(demo_frame()[0], None))
            self.status.set("PREVIEW - Camera is not active.")
            return
        try:
            session = CaptureSession(self.name.get(), self.selected_fields(), self.role)
            self.worker = CameraWorker(self.camera_index, session)
            self.worker.start()
            self.job = self.root.after(40, self.poll_camera)
        except (ValueError, RuntimeError, OSError, KeyError, sqlite3.Error) as error:
            self.status.set(str(error))

    def cancel_camera_redraw(self):
        if self.resize_job is not None:
            self.root.after_cancel(self.resize_job)
            self.resize_job = None

    def queue_camera_redraw(self, _event=None):
        if self.resize_job is None:
            self.resize_job = self.root.after_idle(self.redraw_camera)

    def show_frame(self, frame):
        self.last_frame = frame
        self.redraw_camera()

    def redraw_camera(self):
        self.cancel_camera_redraw()
        view = self.camera_view
        if self.step != 3 or view is None or self.last_frame is None:
            return
        # Measure available space the same way in full screen and small windows.
        width, height = max(1, view.winfo_width()-4), max(1, view.winfo_height()-4)
        size = fitted_size(self.last_frame.shape, width, height)
        interpolation = cv2.INTER_AREA if size[0] < self.last_frame.shape[1] else cv2.INTER_LINEAR
        resized = cv2.resize(self.last_frame, size, interpolation=interpolation)
        ok, encoded = cv2.imencode(".ppm", resized)
        if not ok:
            raise RuntimeError("Could not draw the camera preview.")
        self.photo = tk.PhotoImage(master=self.root, data=encoded.tobytes(), format="PPM")
        center = (view.winfo_width()/2, view.winfo_height()/2)
        if self.camera_image is None:
            view.delete(self.camera_placeholder)
            self.camera_image = view.create_image(*center, image=self.photo, anchor="center")
        else:
            view.itemconfigure(self.camera_image, image=self.photo)
            view.coords(self.camera_image, *center)

    def poll_camera(self):
        self.job = None
        if self.worker is None or self.step != 3:
            return
        try:
            event = self.worker.events.get_nowait()
        except queue.Empty:
            self.job = self.root.after(40, self.poll_camera)
            return
        try:
            if "error" in event:
                self.status.set(event["error"])
                self.next_button.configure(state="disabled")
                self.ready = False
                return
            self.show_frame(event["frame"])
            count = event["count"]
            self.progress.set(f"{count} / 3")
            self.completed = event["done"]
            self.ready = True
            if event["processed"] >= self.pending_request:
                self.busy = False
            self.next_button.configure(state="disabled" if self.busy else "normal",
                                       text="FINISH" if self.completed else "CAPTURE")
            if self.completed:
                self.pose.set("COMPLETE")
                self.status.set("Profile saved. You can now close this window and start recognition.")
                self.back_button.configure(state="disabled")
                self.cancel_button.configure(text="CLOSE")
                return
            self.pose.set(POSES[count] + " - " + PROMPTS[count])
            self.status.set(event["message"])
        except (RuntimeError, tk.TclError, cv2.error) as error:
            self.status.set(str(error))
            self.next_button.configure(state="disabled")
            return
        self.job = self.root.after(40, self.poll_camera)

    def capture(self):
        if self.worker is not None and self.ready and not self.busy and not self.completed:
            self.busy = True
            self.next_button.configure(state="disabled")
            self.status.set("Capturing...")
            self.pending_request += 1
            self.worker.requests = self.pending_request
            self.worker.capture_event.set()

    def close(self):
        try:
            self.stop_camera()
        except RuntimeError as error:
            self.status.set(str(error))
            return
        self.code.set("")
        self.root.destroy()


def check_ui():
    root = tk.Tk()
    root.withdraw()
    window = RegistrationWindow(root, preview=True)
    assert window.step == 0
    window.next()
    assert window.step == 0 and window.status.get()
    window.name.set("Preview_Test")
    window.next()
    assert window.step == 1
    window.field_vars["Software"].set(True)
    window.field_vars["Mechanics"].set(True)
    window.next()
    assert window.step == 2
    window.code.set("incorrect-test-code")
    window.next()
    assert window.step == 2 and window.role == "USER"
    window.code.set("")
    window.next()
    assert window.step == 3 and window.role == "USER"
    assert window.worker is None and window.progress.get() == "0 / 3"
    window.back()
    assert window.step == 2 and not window.code.get()
    window.close()
    print("Registration UI flow passed. No camera opened and no face recorded.")


def main():
    parser = argparse.ArgumentParser(description="Person registration")
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        check_ui()
        return
    # Keep text and window dimensions sharp on scaled displays.
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        pass
    root = tk.Tk()
    if not ACCESS_FILE.is_file():
        from tkinter import messagebox
        root.withdraw()
        messagebox.showinfo('Setup required', 'Open settings.cmd and set role passwords before registering.', parent=root)
        root.destroy()
        return
    RegistrationWindow(root, args.camera)
    root.mainloop()


if __name__ == "__main__":
    main()
