import logging
import signal
import sys
import time
from collections.abc import Callable
from threading import Thread
from typing import Any
from wsgiref.simple_server import WSGIServer

from django.conf import settings
from django_q.cluster import Cluster
from prometheus_client import CollectorRegistry, multiprocess, start_http_server

from taskrunner.schedules import event_updates, playlist_updates

logger = logging.getLogger(__name__)


class MottleCluster(Cluster):
    def __init__(self, *args: Any, **kwargs: dict) -> None:
        super().__init__(*args, **kwargs)
        self.schedules: list = []
        self.prometheus_registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(self.prometheus_registry)

        self.metrics_server: WSGIServer | None = None
        self.metrics_server_thread: Thread | None = None

        signal.signal(signal.SIGHUP, self.sig_handler)

    def add_schedules(self, *funcs: Callable[..., None]) -> None:
        self.schedules = list(funcs)

    def start(self, metrics_server_host: str, metrics_server_port: int) -> None:
        self.metrics_server, self.metrics_server_thread = start_http_server(
            port=metrics_server_port, addr=metrics_server_host, registry=self.prometheus_registry
        )
        logger.info(f"Metrics server started at {metrics_server_host}:{metrics_server_port}")

        if settings.SCHEDULER_ENABLED:
            if self.schedules:
                for func in self.schedules:
                    logger.info(f"Adding schedule {func.__name__}")
                    func()
        else:
            logger.warning("Scheduler is disabled. Skipping schedule setup")

        super().start()

        # A hack to keep the main thread alive. Otherwise:
        # RuntimeError: can't create new thread at interpreter shutdown
        while self.stop_event is not None and not self.stop_event.is_set():
            time.sleep(0.1)

    def sig_handler(self, signum: int, _: Any) -> None:
        logger.info("Shutting down metrics server...")
        if self.metrics_server is not None:
            self.metrics_server.shutdown()

        if self.metrics_server_thread is not None:
            self.metrics_server_thread.join()

        super().sig_handler(signum, _)


def main() -> None:
    metrics_server_host = sys.argv[1]
    metrics_server_port = int(sys.argv[2])

    q = MottleCluster()
    q.add_schedules(playlist_updates, event_updates)
    q.start(metrics_server_host, metrics_server_port)


if __name__ == "__main__":
    logger.debug("Starting taskrunner")
    main()
