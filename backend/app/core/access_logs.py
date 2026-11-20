import asyncio
import logging
from time import perf_counter
from uuid import uuid4

from sentry_sdk.types import Event, Hint
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class SensitivePathFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # Uvicorn renders the request target as the third access log argument.
        args = record.args
        if isinstance(args, tuple) and len(args) == 5 and isinstance(args[2], str):
            target = args[2]
            route = "/password-recovery-html-content/"
            if route in target:
                target = target.split(route, 1)[0] + route + "{email}"
            if "/reset-password?" in target:
                target = target.split("?", 1)[0]
            record.args = (*args[:2], target, *args[3:])
        return True


sensitive_paths = SensitivePathFilter()


def configure_access_logs() -> None:
    logging.getLogger("uvicorn.access").addFilter(sensitive_paths)


logger = logging.getLogger("uvicorn.error")


class RequestLogMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id
        started = perf_counter()
        status = 500
        result = "server_error"

        async def send_with_request_id(message: Message) -> None:
            nonlocal status, result
            if message["type"] == "http.response.start":
                status = message["status"]
                result = "server_error" if status >= 500 else "complete"
                headers = MutableHeaders(scope=message)
                headers["X-Request-ID"] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        except asyncio.CancelledError:
            status = 499
            result = "cancelled"
            raise
        finally:
            route = getattr(scope.get("route"), "path", "unmatched")
            logger.info(
                "request id=%s method=%s route=%s status=%s duration_ms=%.2f result=%s",
                request_id,
                scope["method"],
                route,
                status,
                (perf_counter() - started) * 1000,
                result,
            )


def scrub_error_report(event: Event, _hint: Hint) -> Event:
    # Request paths, bodies, queries and exception messages can carry credentials.
    event.pop("request", None)
    event.pop("user", None)
    event.pop("extra", None)
    event.pop("breadcrumbs", None)
    event.pop("logentry", None)
    for value in event.get("exception", {}).get("values", ()):
        value["value"] = value.get("type", "Application error")
    if event.get("transaction_info", {}).get("source") == "url":
        event["transaction"] = "unmatched"
    spans = event.get("spans")
    if isinstance(spans, list):
        for span in spans:
            span.pop("description", None)
            span.pop("data", None)
    return event
