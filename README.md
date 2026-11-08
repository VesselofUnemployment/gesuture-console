# Gesture Control

A local Windows prototype for face recognition, role-based hand gestures, and desktop control. It uses pretrained OpenCV and MediaPipe models.

## First setup

Install Python 3.12. Open PowerShell in the project folder and run:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
.\.venv\Scripts\python.exe tools\download_face_models.py
.\.venv\Scripts\python.exe tools\download_face_model.py
.\.venv\Scripts\python.exe tools\download_hand_model.py
```

The last two downloads enable the animated face mesh and hand gestures. Normal recognition runs locally.

1. Open `settings.cmd` and choose separate OPERATOR, SUPERVISOR, and ADMIN passwords.
2. Open `register.cmd` to enroll your face. Enter the ADMIN password at **ACCESS LEVEL** and capture the three prompted poses.
3. Open `start.cmd` to start recognition. `face_demo.cmd` opens the introductory detection-only demo.

A fresh checkout has no profiles, passwords, or app targets. Use the current ADMIN password to save later settings changes. Leave both new-password fields blank for roles whose passwords you want to keep. To add a person while recognition is running, use **Quit / release camera** in the tray first.

## Roles

| Capability | Minimum role |
| --- | --- |
| Show a recognized person's profile | USER |
| Hand tracking and 🤟 to bring the camera forward | OPERATOR |
| Management panel and CONTROL MODE | SUPERVISOR |
| App shortcuts and SESSION START | ADMIN |

A blank password at enrollment gives the USER role. SUPERVISOR uses `sudo` for profile changes and can manage USER and OPERATOR profiles. To change a role from the panel, use `user edit NAME role ROLE`. Type `help` for commands and `vision status` for diagnostics. `user NAME recapture` refreshes face samples in the existing camera window.

## Configure apps

- **App Shortcuts** in `settings.cmd` assigns an `.exe`, an `.lnk`, or a Store app to 1–5 and **Fist**.
- **Session Start** defines a separate list of up to five apps. Hold the index and middle fingers close together and point them up, with your thumb extended and the ring and little fingers folded.
- **Clear** leaves a slot empty. Save each list separately and restart recognition from the tray to apply changes.

While recognized as ADMIN with CONTROL MODE off, hold one pose for about one second and lower your hand between commands. An empty assignment does nothing. The session batch checks for already running executable paths and Store app identities. It suppresses repeat requests for ten seconds, including Windows shortcuts. Targets open with normal user permissions.

## Desktop control

Choose **CONTROL MODE / OFF** in the camera window or **CONTROL MODE / hand desktop** in the tray when recognized as SUPERVISOR or ADMIN. Point with your index finger to move the mouse. Pinch the thumb and index finger to click. Hold an open palm to open the wheel, then aim and pinch to select.

The wheel provides workspace switching, media controls, region capture, a system HUD, air drawing, and window control. Window control supports movement, edge placement, and two-hand proportional resizing. A fist returns to the hand mouse and **Esc** disables CONTROL MODE. Brief hand loss preserves the current tool for up to three seconds while stopping all movement and selections.

## Camera and local data

In the camera view **X** or **Esc** sends the app to the tray. Choose **Quit / release camera** to close it. Optional Windows login startup is managed by `tools/startup.py` with `status`, `enable`, or `disable`.

Recognition uses an initial similarity threshold of 0.65 and a 0.05 margin between identities. A continuous face can use a 0.60 continuation threshold for up to one second from a strong match. Dark or uniform frames pause inference. The prototype has no liveness detection.

Local profiles, face embeddings, salted role-password verifiers, and app settings are created in `data/`. This directory is ignored by Git. Camera frames are transient. REGION CAPTURE saves a screen PNG only on request in `captures/`. Protect the data folder and backups because they contain biometric records.

## Development

```powershell
.\.venv\Scripts\python.exe -B src\recognize.py --check
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p "test_*.py"
```

The recognition check uses downloaded models with the camera closed. Tests use temporary data. See [architecture](docs/ARCHITECTURE.md).
