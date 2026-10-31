import logging
import signal
import time
from typing import TYPE_CHECKING, Any

from django.conf import settings
from django_q.cluster import Cluster

from taskrunner.schedules import event_updates, playlist_updates

if TYPE_CHECKING:
    from collections.abc import Callable


logger = logging.getLogger(__name__)


class MottleCluster(Cluster):
    def __init__(self, *args: Any, **kwargs: dict) -> None:
        super().__init__(*args, **kwargs)
        self.schedules: list = []

        signal.signal(signal.SIGHUP, self.sig_handler)

    def add_schedules(self, *funcs: Callable[..., None]) -> None:
        self.schedules = list(funcs)

    def run(self) -> None:
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


def main() -> None:
    q = MottleCluster()
    q.add_schedules(playlist_updates, event_updates)
    q.run()


if __name__ == "__main__":
    logger.debug("Starting taskrunner")
    main()
