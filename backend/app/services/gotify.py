import httpx

from app.config import get_settings


async def send_notification(title: str, message: str, priority: int = 5) -> None:
    """Posts one message to the configured Gotify server. Raises RuntimeError if gotify_url/
    gotify_token aren't both set, and raises on any non-2xx response (httpx.raise_for_status) -
    callers decide whether a failure here should be logged-and-swallowed (the scheduled job) or
    surfaced to the admin (the "send test notification" button)."""
    settings = get_settings()
    if not settings.gotify_url or not settings.gotify_token:
        raise RuntimeError("Gotify is not configured (gotify_url/gotify_token)")

    url = f"{settings.gotify_url.rstrip('/')}/message"
    async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as client:
        resp = await client.post(
            url,
            params={"token": settings.gotify_token},
            json={"title": title, "message": message, "priority": priority},
        )
        resp.raise_for_status()
