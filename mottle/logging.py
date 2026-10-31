from datetime import datetime
from typing import TYPE_CHECKING, Any

from json_log_formatter import BUILTIN_ATTRS, JSONFormatter

if TYPE_CHECKING:
    import logging

BUILTIN_ATTRS.add("taskName")


class MottleJSONFormatter(JSONFormatter):
    def json_record(self, message: str, extra: dict, record: logging.LogRecord) -> dict:
        new_extra: dict[str, Any] = {}

        new_extra["timestamp"] = datetime.fromtimestamp(record.created).isoformat(timespec="microseconds") + "Z"  # noqa: DTZ006
        new_extra["level"] = record.levelname
        new_extra["logger"] = record.name
        new_extra["module"] = record.module
        new_extra["function"] = record.funcName
        new_extra["task"] = record.taskName
        new_extra["stack_trace"] = self.formatException(record.exc_info) if record.exc_info else None
        new_extra["message"] = message

        new_extra["extra"] = str(extra) if extra else None
        return new_extra
