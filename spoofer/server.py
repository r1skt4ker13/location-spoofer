"""Local web API + UI. Bound to 127.0.0.1 only: nothing on the network can drive your phone."""
import os
import sys
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from spoofer.device import DeviceManager, SpooferError


def _web_dir() -> Path:
    # PyInstaller unpacks bundled data under sys._MEIPASS.
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base / "spoofer" / "web"


class Target(BaseModel):
    udid: str


class Point(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class Teleport(Target, Point):
    pass


class Walk(Target):
    points: list[Point] = Field(min_length=2, max_length=500)
    speed: float = Field(gt=0, le=60, description="metres per second")


def create_app(manager: DeviceManager) -> FastAPI:
    app = FastAPI(title="Location Spoofer", docs_url=None, redoc_url=None)

    def call(coro, timeout: float = 120):
        try:
            return manager.run(coro, timeout)
        except SpooferError as e:
            raise HTTPException(400, str(e)) from None

    @app.get("/")
    def index():
        return FileResponse(_web_dir() / "index.html")

    @app.get("/api/devices")
    def devices():
        return call(manager.list_devices(), timeout=30)

    @app.post("/api/prepare")
    def prepare(body: Target):
        # Mounting the developer disk image downloads it the first time: allow a few minutes.
        return call(manager.prepare(body.udid), timeout=600)

    @app.post("/api/location")
    def teleport(body: Teleport):
        call(manager.teleport(body.udid, body.lat, body.lon))
        return manager.state

    @app.post("/api/walk")
    def walk(body: Walk):
        call(manager.walk(body.udid, [(p.lat, p.lon) for p in body.points], body.speed))
        return manager.state

    @app.post("/api/clear")
    def clear():
        call(manager.clear(), timeout=30)
        return manager.state

    @app.get("/api/status")
    def status():
        return manager.state

    @app.post("/api/quit")
    def quit_app():
        """Restore the real location and exit. The packaged Mac app has no terminal to Ctrl+C."""
        manager.shutdown()
        threading.Timer(0.5, os._exit, args=(0,)).start()
        return {"ok": True}

    return app
