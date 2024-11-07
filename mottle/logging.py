import logging
from datetime import datetime
from typing import Any

from json_log_formatter import BUILTIN_ATTRS, JSONFormatter

BUILTIN_ATTRS.add("taskName")


class MottleJSONFormatter(JSONFormatter):
    def extra_from_record(self, record: logging.LogRecord) -> dict:
        return {
            attr_name: record.__dict__[attr_name] for attr_name in record.__dict__ if attr_name not in BUILTIN_ATTRS
        }

    def json_record(self, message: str, extra: dict, record: logging.LogRecord) -> dict:
        new_extra: dict[str, Any] = {}

        new_extra["timestamp"] = datetime.fromtimestamp(record.created).isoformat(timespec="microseconds") + "Z"
        new_extra["level"] = record.levelname
        new_extra["logger"] = record.name
        new_extra["module"] = record.module
        new_extra["function"] = record.funcName
        new_extra["task"] = record.taskName
        new_extra["stack_trace"] = self.formatException(record.exc_info) if record.exc_info else None
        new_extra["message"] = message

        new_extra["extra"] = f"{extra}"
        return new_extra
