import logging
import signal
import sys
import time
from threading import Thread
from typing import Any, Optional
from wsgiref.simple_server import WSGIServer

from django_q.cluster import Cluster
from prometheus_client import start_http_server

logger = logging.getLogger(__name__)


class MottleCluster(Cluster):
    def __init__(self, *args: Any, **kwargs: dict) -> None:
        super().__init__(*args, **kwargs)
        self.metrics_server: Optional[WSGIServer] = None
        self.metrics_server_thread: Optional[Thread] = None
        signal.signal(signal.SIGHUP, self.sig_handler)

    def start(self, metrics_server_host: str, metrics_server_port: int) -> None:
        self.metrics_server, self.metrics_server_thread = start_http_server(
            port=metrics_server_port, addr=metrics_server_host
        )
        logger.info(f"Metrics server started at {metrics_server_host}:{metrics_server_port}")

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
    q.start(metrics_server_host, metrics_server_port)
