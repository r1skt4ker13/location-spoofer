"""Entry point: `python -m spoofer` (or the packaged app). Opens the map in your browser."""
import atexit
import logging
import signal
import socket
import sys
import threading
import webbrowser

import uvicorn

from spoofer.device import DeviceManager
from spoofer.server import create_app

PREFERRED_PORT = 8765


def _free_port() -> int:
    for port in (PREFERRED_PORT, 0):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return s.getsockname()[1]
            except OSError:
                continue
    raise RuntimeError("no free port")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
    manager = DeviceManager()
    # Whatever happens, don't leave the phone stuck at a fake location.
    atexit.register(manager.shutdown)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

    port = _free_port()
    url = f"http://127.0.0.1:{port}"
    print(f"\n  Location Spoofer is running at {url}\n  Keep this window open while spoofing. Press Ctrl+C to quit.\n")
    threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    uvicorn.run(create_app(manager), host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
