#!/usr/bin/env python3
"""Serve an isolated read-only 3D view of the coffee live service."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from urllib.parse import urlparse

from aiohttp import ClientSession, WSMsgType, web


HERE = Path(__file__).resolve().parent
COFFEE = HERE.parent
WEB_VENDOR = COFFEE / "web" / "vendor"
BROWSER_ASSETS = COFFEE / "visual_assets" / "browser"
GENERATED_EARRING = (
    COFFEE.parents[1]
    / "thoughts"
    / "taras"
    / "research"
    / "coffee-quality"
    / "object-generation"
    / "results"
    / "gemini"
    / "earring"
    / "render"
    / "object.glb"
)

REQUIRED_STATIC_FILES = {
    "/": HERE / "index.html",
    "/app.js": HERE / "app.js",
    "/geometry.mjs": HERE / "geometry.mjs",
    "/three/three.module.js": WEB_VENDOR / "three.module.js",
    "/three/three.core.js": WEB_VENDOR / "three.core.js",
    "/three/OrbitControls.js": WEB_VENDOR / "OrbitControls.js",
    "/three/RoomEnvironment.js": WEB_VENDOR / "RoomEnvironment.js",
    "/three/loaders/GLTFLoader.js": BROWSER_ASSETS / "vendor" / "loaders" / "GLTFLoader.js",
    "/three/utils/BufferGeometryUtils.js": BROWSER_ASSETS / "vendor" / "utils" / "BufferGeometryUtils.js",
    "/assets/bean_good_lod.glb": BROWSER_ASSETS / "bean_good_lod.glb",
    "/assets/bean_black_lod.glb": BROWSER_ASSETS / "bean_black_lod.glb",
    "/assets/bean_insect_lod.glb": BROWSER_ASSETS / "bean_insect_lod.glb",
    "/assets/bean_broken_lod.glb": BROWSER_ASSETS / "bean_broken_lod.glb",
}
OPTIONAL_STATIC_FILES = {
    "/assets/generated_earring.glb": GENERATED_EARRING,
}


def available_static_files() -> dict[str, Path]:
    missing = [str(path) for path in REQUIRED_STATIC_FILES.values() if not path.is_file()]
    if missing:
        raise FileNotFoundError("Missing preview assets: " + ", ".join(missing))
    return {
        **REQUIRED_STATIC_FILES,
        **{route: path for route, path in OPTIONAL_STATIC_FILES.items() if path.is_file()},
    }


def asset_manifest(static_files: dict[str, Path]) -> dict:
    assets = {}
    for route, path in static_files.items():
        if path.suffix != ".glb":
            continue
        content = path.read_bytes()
        assets[route] = {
            "bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }
    return {"assets": assets, "total_bytes": sum(item["bytes"] for item in assets.values())}


def trusted_browser_origin(origin: str | None, scheme: str, host: str) -> bool:
    if not origin:
        return False
    supplied = urlparse(origin)
    requested = urlparse(f"{scheme}://{host}")
    return (
        requested.hostname in {"127.0.0.1", "localhost", "::1"}
        and supplied.scheme == requested.scheme
        and supplied.netloc.lower() == requested.netloc.lower()
    )


class PreviewService:
    def __init__(self, backend: str):
        parsed = urlparse(backend)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or not parsed.port:
            raise ValueError("Backend must be an absolute HTTP or HTTPS URL with an explicit port.")
        self.backend = backend.rstrip("/")
        self.backend_origin = f"{parsed.scheme}://{parsed.netloc}"
        ws_scheme = "wss" if parsed.scheme == "https" else "ws"
        self.backend_ws = f"{ws_scheme}://{parsed.netloc}/ws"
        self.session: ClientSession | None = None

    async def lifecycle(self, app):
        self.session = ClientSession()
        try:
            yield
        finally:
            await self.session.close()

    async def health(self, request):
        try:
            async with self.session.get(f"{self.backend}/health", timeout=2) as response:
                payload = await response.json()
                return web.json_response({"preview": "ready", "backend": payload}, status=response.status)
        except Exception as exc:
            return web.json_response(
                {"preview": "ready", "backend": {"status": "unavailable", "error": str(exc)}},
                status=503,
            )

    async def websocket(self, request):
        if not trusted_browser_origin(request.headers.get("Origin"), request.scheme, request.host):
            raise web.HTTPForbidden(text="WebSocket Origin must match this loopback preview.")
        browser = web.WebSocketResponse(heartbeat=20, max_msg_size=2048)
        await browser.prepare(request)
        try:
            async with self.session.ws_connect(
                self.backend_ws,
                origin=self.backend_origin,
                heartbeat=20,
                max_msg_size=2 * 1024 * 1024,
            ) as backend:
                async def reject_browser_commands():
                    async for message in browser:
                        if message.type in {WSMsgType.TEXT, WSMsgType.BINARY}:
                            await browser.close(code=1008, message=b"This preview is read-only")
                            return

                reader = asyncio.create_task(reject_browser_commands())
                try:
                    async for message in backend:
                        if browser.closed:
                            break
                        if message.type == WSMsgType.TEXT:
                            await browser.send_str(message.data)
                        elif message.type == WSMsgType.BINARY:
                            await browser.send_bytes(message.data)
                        elif message.type in {WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.ERROR}:
                            break
                finally:
                    reader.cancel()
                    await asyncio.gather(reader, return_exceptions=True)
        except Exception as exc:
            if not browser.closed:
                await browser.send_json({"type": "preview_error", "error": str(exc)})
        finally:
            if not browser.closed:
                await browser.close()
        return browser


def build_app(backend: str) -> web.Application:
    static_files = available_static_files()
    service = PreviewService(backend)
    manifest = asset_manifest(static_files)
    app = web.Application(client_max_size=2048)
    app.cleanup_ctx.append(service.lifecycle)

    async def static_file(request):
        return web.FileResponse(static_files[request.path])

    async def manifest_handler(request):
        return web.json_response(manifest)

    app.add_routes(
        [web.get(path, static_file) for path in static_files]
        + [
            web.get("/asset-manifest.json", manifest_handler),
            web.get("/health", service.health),
            web.get("/ws", service.websocket),
        ]
    )
    return app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", choices=["127.0.0.1"], default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8895)
    parser.add_argument("--backend", default="http://127.0.0.1:8892")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("Port must be between 1 and 65535.")
    print(f"Read-only live backend: {args.backend}", flush=True)
    web.run_app(build_app(args.backend), host=args.host, port=args.port, access_log=None)


if __name__ == "__main__":
    main()
