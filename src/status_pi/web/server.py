"""The web UI: the only way to configure or control a device with no buttons.

Small aiohttp app, LAN-only by default.  Every mutating route nudges the tick
loop so the panel reflects the change immediately rather than on the next
poll.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

log = logging.getLogger(__name__)

STATIC = Path(__file__).parent / "static"
MAX_STATUS_CHARS = 24
MAX_TIMER_SECONDS = 24 * 3600


def _json(payload, status: int = 200):
    from aiohttp import web

    return web.json_response(payload, status=status)


def build_app(app_state):
    from aiohttp import web

    routes = web.RouteTableDef()

    @web.middleware
    async def auth(request, handler):
        token = app_state.config.web.auth_token
        if not token or request.path == "/healthz":
            return await handler(request)
        supplied = (
            request.headers.get("X-Auth-Token")
            or request.query.get("token")
            or request.cookies.get("status_pi_token")
        )
        if supplied != token:
            return web.json_response({"error": "unauthorised"}, status=401)
        response = await handler(request)
        response.set_cookie("status_pi_token", token, max_age=31536000, samesite="Lax")
        return response

    @routes.get("/")
    async def index(request):
        return web.FileResponse(STATIC / "index.html")

    @routes.get("/healthz")
    async def healthz(request):
        return web.Response(text="ok")

    @routes.get("/preview.png")
    async def preview(request):
        return web.Response(
            body=app_state.preview_png(),
            content_type="image/png",
            headers={"Cache-Control": "no-store"},
        )

    @routes.get("/api/state")
    async def get_state(request):
        return _json(app_state.describe())

    @routes.post("/api/status")
    async def set_status(request):
        body = await request.json()
        text = str(body.get("text", "")).strip()[:MAX_STATUS_CHARS]
        if not text:
            app_state.runtime.status.text = ""
            app_state.runtime.status.expires_at = None
        else:
            minutes = body.get("minutes")
            app_state.runtime.status.text = text
            app_state.runtime.status.color = str(body.get("color", "amber"))
            app_state.runtime.status.expires_at = (
                time.time() + float(minutes) * 60 if minutes else None
            )
        app_state.runtime.save()
        app_state.wake()
        return _json(app_state.describe()["status"])

    @routes.delete("/api/status")
    async def clear_status(request):
        app_state.runtime.status.text = ""
        app_state.runtime.status.expires_at = None
        app_state.runtime.save()
        app_state.wake()
        return _json({"text": ""})

    @routes.post("/api/timer")
    async def timer_action(request):
        body = await request.json()
        action = str(body.get("action", "start"))
        timer = app_state.runtime.timer
        if action == "start":
            seconds = int(float(body.get("seconds", 0)))
            if not 0 < seconds <= MAX_TIMER_SECONDS:
                return _json({"error": "seconds out of range"}, status=400)
            timer.start(seconds, str(body.get("label", "")).strip()[:MAX_STATUS_CHARS])
            app_state.runtime.timer_done_at = None
        elif action == "pause":
            timer.pause()
        elif action == "resume":
            timer.resume()
        elif action == "stop":
            timer.stop()
            app_state.runtime.timer_done_at = None
        else:
            return _json({"error": "unknown action"}, status=400)
        app_state.runtime.save()
        app_state.wake()
        return _json(app_state.describe()["timer"])

    @routes.get("/api/config")
    async def get_config(request):
        return _json(app_state.config.to_dict(redact_secrets=True))

    @routes.post("/api/config")
    async def post_config(request):
        try:
            updates = await request.json()
        except Exception:  # noqa: BLE001
            return _json({"error": "invalid json"}, status=400)
        try:
            app_state.apply_config(updates)
        except Exception as exc:  # noqa: BLE001
            log.exception("config update failed")
            return _json({"error": str(exc)}, status=400)
        return _json(app_state.config.to_dict(redact_secrets=True))

    @routes.get("/api/ha/calendars")
    async def ha_calendars(request):
        """List the calendar entities Home Assistant can see, so the UI can
        offer them instead of asking the user to type an entity id."""
        import aiohttp

        ha = app_state.config.home_assistant
        if not (ha.url and ha.token):
            return _json({"error": "Home Assistant is not configured yet"}, status=400)
        url = ha.url.rstrip("/") + "/api/calendars"
        headers = {"Authorization": "Bearer %s" % ha.token}
        try:
            timeout = aiohttp.ClientTimeout(total=15)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, headers=headers) as response:
                    if response.status == 401:
                        return _json({"error": "Home Assistant rejected the token"}, status=400)
                    response.raise_for_status()
                    return _json(await response.json())
        except Exception as exc:  # noqa: BLE001
            return _json({"error": str(exc)}, status=502)

    @routes.post("/api/calendar/refresh")
    async def refresh_calendar(request):
        app_state.calendar.refresh_soon()
        return _json({"ok": True})

    @routes.post("/api/calendar/hide")
    async def hide_event(request):
        """Hide one event for good -- the escape hatch for the iCal feed
        showing a meeting you actually declined."""
        body = await request.json()
        uid = str(body.get("uid", "")).strip()
        if not uid:
            return _json({"error": "uid required"}, status=400)
        hidden = list(app_state.config.calendar.hidden_uids or [])
        if uid not in hidden:
            hidden.append(uid)
        app_state.apply_config({"calendar": {"hidden_uids": hidden}})
        return _json({"hidden_uids": hidden})

    app = web.Application(middlewares=[auth])
    app.add_routes(routes)
    if STATIC.exists():
        app.router.add_static("/static/", STATIC)
    return app


async def start_web(app_state):
    """Start the UI; a failure here must never take the panel down."""
    try:
        from aiohttp import web
    except ImportError:
        log.warning("aiohttp not installed -- web UI disabled")
        return None
    cfg = app_state.config.web
    app = build_app(app_state)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    try:
        site = web.TCPSite(runner, cfg.host, cfg.port)
        await site.start()
    except OSError as exc:
        log.error("web UI could not bind %s:%s (%s)", cfg.host, cfg.port, exc)
        await runner.cleanup()
        return None
    log.info("web UI on http://%s:%s", cfg.host, cfg.port)
    return runner
