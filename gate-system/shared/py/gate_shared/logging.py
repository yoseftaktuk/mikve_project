import logging
import sys


def configure_logging(service_name: str, level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger().info("logging_configured service=%s", service_name)

