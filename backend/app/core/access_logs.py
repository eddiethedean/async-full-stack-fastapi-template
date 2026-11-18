import logging


class SensitivePathFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # Uvicorn renders the request target as the third access log argument.
        args = record.args
        if isinstance(args, tuple) and len(args) == 5 and isinstance(args[2], str):
            target = args[2]
            for route in ("/password-recovery/", "/password-recovery-html-content/"):
                if route in target:
                    target = target.split(route, 1)[0] + route + "{email}"
                    break
            if "/reset-password?" in target:
                target = target.split("?", 1)[0]
            record.args = (*args[:2], target, *args[3:])
        return True


sensitive_paths = SensitivePathFilter()


def configure_access_logs() -> None:
    logging.getLogger("uvicorn.access").addFilter(sensitive_paths)
