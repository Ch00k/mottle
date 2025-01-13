import sys

import sentry_sdk


class Sentry:
    def report(self) -> None:
        sentry_sdk.capture_exception(sys.exc_info())
