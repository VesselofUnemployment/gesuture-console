# Architecture

```text
Single camera → LatestFrames (latest frame mailbox)
                 ├─ face worker → YuNet / SFace → profile, authority, animation
                 └─ HandPipeline → MediaPipe → gesture and raw landmarks
                                               ↓
                               Recognition UI checks freshness and role
                                 ├─ Tk camera, terminal, and notices
                                 ├─ AppGesture → AppLauncher → Windows targets
                                 └─ DesktopControl → policy → native overlays / Windows
```

| Area | Primary modules |
| --- | --- |
| Entry, models, and matching | `recognize.py`, `face_enrollment.py`, `person_store.py`, `face_continuation.py` |
| Camera, workers, and UI | `recognition_ui.py`, `latest_frames.py`, `camera_idle.py`, `vision_runtime.py` |
| Animation and frames | `face_tracking.py`, `recognition_sequence.py`, `vision_hud.py`, `bitmap_font.py` |
| Hands and gesture geometry | `hand_pipeline.py`, `hand_tracking.py`, `finger_count.py`, `session_pose.py` |
| Authority and profiles | `recognition_presence.py`, `gesture_permissions.py`, `profile_store.py`, `admin_commands.py` |
| Desktop actions | `desktop_control.py`, `desktop_tools.py`, `desktop_windows.py` |
| Overlays, metrics, and capture | `control_overlay.py`, `desktop_services.py` |
| Gesture shortcuts and session launch | `app_gestures.py`, `app_launcher.py`, `session_start.py` |
| Enrollment, recapture, and settings | `register_ui.py`, `recapture_ui.py`, `settings_ui.py`, `settings_store.py` |
| Terminal, notices, and background | `admin_terminal.py`, `desktop_notice.py`, `native_notice_window.py`, `background_runtime.py`, `windows_tray.py` |
| Project root | `project_paths.py` resolves paths relative to the source |

## Data flow and boundaries

SFace compares embeddings, and SQLite supplies current profile metadata, roles, and statistics. One current active identity can grant app authority. Hand results are checked again for role, generation, and freshness. Tracking memory can preserve a tool's state during loss, while stale landmarks cannot trigger commands. The animation only displays the recognition decision.

Overlays are click-through, nonactivating windows. Mouse, media, and desktop actions require the corresponding control state. Hardware metrics and PNG saves run on separate workers. Windows input sequences are predefined, and the management terminal interprets app commands.

Settings are separate from biometric storage. The current ADMIN password authorizes updates after first setup. Gesture assignments are loaded at application startup. SESSION START loads a separate, initially empty list.

Camera frames and hand history are transient. An explicit REGION CAPTURE command can produce a screen PNG. Runtime data stays outside Git.

## Reliability

- `storage_runtime.py` provides a cross-process lock and a recovery journal tied to SQLite commits.
- `profile_store.py` caches embeddings by file signature, reads current SQL metadata, and excludes invalid records from matching.
- `presence_writer.py` writes login statistics from a bounded, ordered queue.
- `runtime_lifecycle.py` manages the camera lease, independent cleanup steps, rotating logs, and on-demand process metrics.
- `RecognitionWindow.poll` contains recoverable errors. Tk stays responsive while shutdown waits for the in-flight camera read.

See [README](../README.md) for setup and operational constraints.
