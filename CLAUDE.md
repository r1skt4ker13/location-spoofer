# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A local Mac/Windows app that sets a USB- or Wi-Fi-connected iPhone's location to a point picked on a map, so Find My shows that point. It drives Apple's developer location-simulation service through [pymobiledevice3](https://github.com/doronz88/pymobiledevice3).

This repo previously held an unrelated job-search agent; its final state is tagged `job-agent-final`.

## Commands

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt          # runtime deps (exact pins)
python -m spoofer                        # run: local server on 127.0.0.1:8765 (or a free port), opens the browser
pip install -r requirements-build.txt && pyinstaller --noconfirm spoofer.spec   # package for this OS -> dist/
```

There is no test suite. Pure helpers like `_interpolate` in `spoofer/device.py` can be checked with `python -c`. Releases are built by `.github/workflows/build.yml` when a `v*` tag is pushed (macOS + Windows). A Mac can't build the Windows exe, so that build only happens in CI.

## Architecture

- **`spoofer/device.py`:** all phone communication.
  - **iOS 17+ tunnel:** the location service is reached through `UserspaceRsdTunnel`, pymobiledevice3's in-process tunnel, which needs no root or admin. It's then opened as `DvtProvider` → `LocationSimulation`.
  - **One event-loop thread:** the userspace tunnel is a **process-global singleton and isn't thread-safe**. So `DeviceManager` runs every device coroutine on one dedicated event-loop thread, and sync callers go through `DeviceManager.run()`. Only one phone can be spoofed at a time.
  - **Session stays open:** the simulated location lasts only while the DVT connection stays open, so the session is kept open. A keepalive task re-sends the point every 20 s and reconnects if the connection dropped, for example a Wi-Fi blip.
  - **`prepare()`:** one-time setup. It checks trust, Developer Mode (and reveals the toggle in Settings via AMFI), Wi-Fi connections, and the developer disk image, mounted with `auto_mount` over the tunnel.
  - **Errors:** library exceptions are turned into user-readable messages by `_friendly()`, raised as `SpooferError`, and returned as HTTP 400s.
- **`spoofer/server.py`:**
  - A FastAPI app bound to **127.0.0.1 only**, so nothing on the network can drive the phone.
  - It serves `spoofer/web/index.html` and a small JSON API: devices, prepare, location, walk, clear, status, quit.
  - Under PyInstaller, `_web_dir()` resolves the web folder through `sys._MEIPASS`.
- **`spoofer/web/index.html`:** a single-file UI.
  - Leaflet is pinned from cdnjs. Map tiles come from OSM, with attribution required.
  - Search uses Nominatim, throttled to ≤1 request/s per its usage policy.
  - Favorites are kept in localStorage.
- **`spoofer/__main__.py`:**
  - Picks a port and opens the browser.
  - Registers `manager.shutdown()` with atexit, so quitting always restores the real location. The `/api/quit` endpoint does the same thing, because the packaged Mac `.app` has no terminal.
- **`spoofer.spec`:** pymobiledevice3 imports its modules lazily, and the tunnel needs the `pmd_*` network-stack packages, so the spec bundles all of them with `collect_all`. Add a package there if the packaged app fails with a `ModuleNotFoundError` the source run doesn't have.

Pin new dependency versions exactly. pymobiledevice3 releases almost daily, so pick one that's at least two weeks old.
