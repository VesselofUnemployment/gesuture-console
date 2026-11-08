"""Pump Tk until asynchronous cleanup actually finishes; never hide a leak."""
import time


def close_app(app, timeout=8.):
    app.close()
    deadline = time.monotonic() + timeout
    while not app.closed and time.monotonic() < deadline:
        app.root.update()
        time.sleep(.005)
    assert app.closed, 'Application did not finish asynchronous cleanup'
    assert app.worker is None or not app.worker.is_alive(), 'Camera worker survived cleanup'
