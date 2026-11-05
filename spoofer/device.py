"""
All iPhone communication, via pymobiledevice3.

iOS exposes a developer "simulate location" service (the one Xcode uses). While a
simulation is active, the whole device, including Find My, reports the simulated
coordinates. On iOS 17+ the service is reached through an RSD tunnel; we use
pymobiledevice3's in-process userspace tunnel, which needs no root/admin.

Constraints that shape this module:
- The userspace tunnel is a process-global singleton and not thread-safe, so every
  device call runs on ONE dedicated asyncio loop thread (`DeviceManager.loop`).
- The simulated location only holds while our DVT connection stays open, so the
  connection is kept alive for the whole spoofing session.
"""
import asyncio
import contextlib
import logging
import math
import threading
from contextlib import AsyncExitStack
from typing import Any, Optional

from pymobiledevice3 import usbmux
from pymobiledevice3.exceptions import (
    AlreadyMountedError,
    DeviceHasPasscodeSetError,
    DeviceNotFoundError,
    MuxException,
    NoDeviceConnectedError,
    NotPairedError,
    PairingDialogResponsePendingError,
    PairingError,
    PasswordRequiredError,
    UserDeniedPairingError,
)
from pymobiledevice3.lockdown import create_using_usbmux
from pymobiledevice3.remote.userspace_tunnel import UserspaceRsdTunnel
from pymobiledevice3.services.amfi import AmfiService
from pymobiledevice3.services.dvt.instruments.dvt_provider import DvtProvider
from pymobiledevice3.services.dvt.instruments.location_simulation import LocationSimulation
from pymobiledevice3.services.mobile_image_mounter import auto_mount

logger = logging.getLogger("spoofer.device")

KEEPALIVE_SECONDS = 10     # re-send the current point this often; also detects dropped connections
CONNECT_TIMEOUT = 20       # opening the tunnel + location channel
SEND_TIMEOUT = 10          # one location update
WALK_TICK_SECONDS = 1.0    # how often a walk sends a new point
EARTH_RADIUS_M = 6_371_000


class SpooferError(Exception):
    """An error with a message meant to be shown to the user as-is."""


def _friendly(e: BaseException) -> SpooferError:
    if isinstance(e, SpooferError):
        return e
    if isinstance(e, (DeviceNotFoundError, NoDeviceConnectedError)):
        return SpooferError("Phone not found. Plug it in, or make sure it's on the same Wi-Fi as this computer.")
    if isinstance(e, PairingDialogResponsePendingError):
        return SpooferError("Unlock your iPhone and tap \"Trust\" on the prompt, then try again.")
    if isinstance(e, UserDeniedPairingError):
        return SpooferError("You tapped \"Don't Trust\". Unplug and replug the phone, then tap \"Trust\".")
    if isinstance(e, PasswordRequiredError):
        return SpooferError("Unlock your iPhone (enter your passcode), then try again.")
    if isinstance(e, (NotPairedError, PairingError)):
        return SpooferError("This computer isn't trusted by the phone yet. Plug it in with a cable and tap \"Trust\".")
    if isinstance(e, MuxException):
        return SpooferError("Lost the connection to the phone. Plug it back in, or check it's on the same Wi-Fi, then try again.")
    return SpooferError(f"{type(e).__name__}: {e}" if str(e) else type(e).__name__)


async def _lockdown(serial: str, autopair: bool):
    """Lockdown over USB if the phone is plugged in, otherwise over Wi-Fi."""
    devices = await usbmux.list_devices()
    types = {d.connection_type for d in devices if d.serial == serial}
    if not types:
        raise SpooferError("Phone not found. Plug it in, or make sure it's on the same Wi-Fi as this computer.")
    conn_type = "USB" if "USB" in types else "Network"
    return await create_using_usbmux(serial=serial, autopair=autopair, connection_type=conn_type)


def _interpolate(points: list[tuple[float, float]], speed_mps: float) -> list[tuple[float, float]]:
    """Points spaced `speed_mps * WALK_TICK_SECONDS` metres apart along the polyline."""
    step = max(speed_mps * WALK_TICK_SECONDS, 0.5)
    out: list[tuple[float, float]] = [points[0]]
    for (lat1, lon1), (lat2, lon2) in zip(points, points[1:]):
        # Equirectangular approximation: accurate to well under 1% for the distances walked here.
        x = math.radians(lon2 - lon1) * math.cos(math.radians((lat1 + lat2) / 2))
        y = math.radians(lat2 - lat1)
        dist = math.hypot(x, y) * EARTH_RADIUS_M
        n = max(int(dist // step), 1)
        out.extend((lat1 + (lat2 - lat1) * i / n, lon1 + (lon2 - lon1) * i / n) for i in range(1, n + 1))
    return out


class DeviceManager:
    """Owns the device loop thread and the (single) active spoofing session."""

    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self.loop.run_forever, name="device-loop", daemon=True)
        self._thread.start()
        self._closed = False

        self._stack: Optional[AsyncExitStack] = None
        self._sim: Optional[LocationSimulation] = None
        self._serial: Optional[str] = None        # phone the open session talks to
        self._target: Optional[str] = None        # phone we're spoofing; survives dropped sessions
        self._walk_task: Optional[asyncio.Task] = None
        self._keepalive_task: Optional[asyncio.Task] = None

        self.state: dict[str, Any] = {
            "connected_udid": None,
            "location": None,     # [lat, lon] currently being reported
            "mode": "off",        # off | static | walking
            "walk_remaining_s": None,
            "error": None,
        }

    # --- sync entry points (called from the web server threads) ---------------------------

    def run(self, coro, timeout: float = 120):
        try:
            return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)
        except Exception as e:
            raise _friendly(e) from e

    def shutdown(self) -> None:
        """Restore the real location and close the tunnel (called on app exit). Idempotent."""
        if self._closed:
            return
        self._closed = True
        with contextlib.suppress(Exception):
            self.run(self.clear(), timeout=10)
        self.loop.call_soon_threadsafe(self.loop.stop)

    # --- device discovery & setup -------------------------------------------------------

    async def list_devices(self) -> list[dict]:
        found: dict[str, set[str]] = {}
        for d in await usbmux.list_devices():
            found.setdefault(d.serial, set()).add(d.connection_type)

        result = []
        for serial, types in found.items():
            info: dict[str, Any] = {
                "udid": serial,
                "connection": "USB" if "USB" in types else "Wi-Fi",
                "name": None, "ios": None,
                "trusted": False, "developer_mode": None, "wifi_enabled": None,
            }
            try:
                lockdown = await create_using_usbmux(
                    serial=serial, autopair=False, connection_type="USB" if "USB" in types else "Network"
                )
                try:
                    info["name"] = lockdown.all_values.get("DeviceName")
                    info["ios"] = lockdown.product_version
                    info["trusted"] = lockdown.paired
                    if lockdown.paired:
                        info["developer_mode"] = await lockdown.get_developer_mode_status()
                        info["wifi_enabled"] = await lockdown.get_enable_wifi_connections()
                finally:
                    await lockdown.close()
            except Exception as e:  # unpaired/locked phones still get listed
                logger.debug("could not query %s: %r", serial, e)
            result.append(info)
        return result

    async def prepare(self, serial: str) -> dict:
        """One-time setup: trust, Developer Mode, Wi-Fi connections, developer disk image.
        Returns {"done": bool, "steps": [...], "next": str|None}."""
        steps: list[str] = []
        lockdown = await _lockdown(serial, autopair=True)  # shows the "Trust" prompt if needed
        try:
            steps.append("Computer is trusted")

            if not await lockdown.get_developer_mode_status():
                try:
                    await AmfiService(lockdown).reveal_developer_mode_option_in_ui()
                except DeviceHasPasscodeSetError:
                    pass
                return {
                    "done": False,
                    "steps": steps,
                    "next": (
                        "On your iPhone open Settings → Privacy & Security → Developer Mode, turn it on "
                        "and tap Restart. After the phone restarts, unlock it, tap \"Turn On\", then "
                        "click Prepare again."
                    ),
                }
            steps.append("Developer Mode is on")

            if not await lockdown.get_enable_wifi_connections():
                await lockdown.set_enable_wifi_connections(True)
            steps.append("Wi-Fi connections enabled")
        finally:
            await lockdown.close()

        # The developer disk image must be mounted for the location service to exist.
        # On iOS 17+ mounting needs the tunnel, so mount through it.
        await self._close_session()
        tunnel = UserspaceRsdTunnel(serial=serial)
        rsd = await tunnel.aopen()
        try:
            with contextlib.suppress(AlreadyMountedError):
                await auto_mount(rsd)
            steps.append("Developer disk image mounted")
        finally:
            await tunnel.aclose()
        return {"done": True, "steps": steps, "next": None}

    # --- spoofing session ---------------------------------------------------------------

    async def _open_session(self, serial: str) -> None:
        if self._sim is not None and self._serial == serial:
            return
        await self._close_session()
        stack = AsyncExitStack()

        async def connect() -> LocationSimulation:
            rsd = await stack.enter_async_context(UserspaceRsdTunnel(serial=serial))
            dvt = await stack.enter_async_context(DvtProvider(rsd))
            return await stack.enter_async_context(LocationSimulation(dvt))

        try:
            self._sim = await asyncio.wait_for(connect(), CONNECT_TIMEOUT)
        except BaseException:
            self._sim = None
            with contextlib.suppress(Exception):
                await stack.aclose()
            raise
        types = {d.connection_type for d in await usbmux.list_devices() if d.serial == serial}
        logger.info("session open to %s via %s", serial, "USB" if "USB" in types else "Wi-Fi")
        self._stack, self._serial = stack, serial
        self.state["connected_udid"] = serial
        if self._keepalive_task is None or self._keepalive_task.done():
            self._keepalive_task = asyncio.create_task(self._keepalive())

    async def _close_session(self) -> None:
        stack, self._stack, self._sim, self._serial = self._stack, None, None, None
        self.state["connected_udid"] = None
        if stack is not None:
            with contextlib.suppress(Exception):
                await stack.aclose()

    async def _send(self, serial: str, lat: float, lon: float) -> None:
        """Set a point, reconnecting once if the connection dropped (e.g. Wi-Fi blip)."""
        self._target = serial
        await self._open_session(serial)
        try:
            await asyncio.wait_for(self._sim.set(lat, lon), SEND_TIMEOUT)
        except Exception as e:
            logger.warning("send failed (%r); reconnecting", e)
            await self._close_session()
            await self._open_session(serial)
            await asyncio.wait_for(self._sim.set(lat, lon), SEND_TIMEOUT)
        self.state["location"] = [lat, lon]
        self.state["error"] = None

    async def _keepalive(self) -> None:
        while True:
            await asyncio.sleep(KEEPALIVE_SECONDS)
            loc, serial = self.state["location"], self._target
            if self.state["mode"] != "static" or loc is None or serial is None:
                continue
            had_error = bool(self.state["error"])
            try:
                await self._send(serial, *loc)
                if had_error:
                    logger.info("reconnected")
            except Exception as e:
                # Keep retrying every tick: the phone may just be asleep or briefly off Wi-Fi.
                logger.warning("keepalive failed: %r", e)
                self.state["error"] = "Lost connection to the phone, retrying… (" + str(_friendly(e)) + ")"

    async def _stop_walk(self) -> None:
        task, self._walk_task = self._walk_task, None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self.state["walk_remaining_s"] = None

    async def teleport(self, serial: str, lat: float, lon: float) -> None:
        await self._stop_walk()
        await self._send(serial, lat, lon)
        self.state["mode"] = "static"

    async def walk(self, serial: str, points: list[tuple[float, float]], speed_mps: float) -> None:
        """Start moving along `points` (first point = start). Returns immediately."""
        if len(points) < 2:
            raise SpooferError("A walk needs a start and a destination.")
        await self._stop_walk()
        path = _interpolate(points, speed_mps)
        await self._send(serial, *path[0])
        self.state["mode"] = "walking"

        async def run() -> None:
            try:
                for i, (lat, lon) in enumerate(path[1:], start=1):
                    self.state["walk_remaining_s"] = round((len(path) - 1 - i) * WALK_TICK_SECONDS)
                    await asyncio.sleep(WALK_TICK_SECONDS)
                    await self._send(serial, lat, lon)
                self.state["mode"] = "static"  # arrived: stay at the destination
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.state["error"] = str(_friendly(e))
                self.state["mode"] = "static"
            finally:
                self.state["walk_remaining_s"] = None

        self._walk_task = asyncio.create_task(run())

    async def clear(self) -> None:
        """Stop spoofing: the phone goes back to its real GPS."""
        await self._stop_walk()
        if self._sim is not None:
            with contextlib.suppress(Exception):
                await self._sim.clear()
        await self._close_session()
        self._target = None
        self.state.update(location=None, mode="off", error=None)
