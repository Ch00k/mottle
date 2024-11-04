import logging
from datetime import datetime, timezone

from json_log_formatter import BUILTIN_ATTRS, JSONFormatter

BUILTIN_ATTRS.add("taskName")


class MottleJSONFormatter(JSONFormatter):
    def extra_from_record(self, record: logging.LogRecord) -> dict:
        return {
            attr_name: record.__dict__[attr_name] for attr_name in record.__dict__ if attr_name not in BUILTIN_ATTRS
        }

    def json_record(self, message: str, extra: dict, record: logging.LogRecord) -> dict:
        extra["timestamp"] = datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(timespec="microseconds")
        extra["level"] = record.levelname
        extra["logger"] = record.name
        extra["module"] = record.module
        extra["function"] = record.funcName
        extra["task"] = record.taskName
        extra["stack_trace"] = self.formatException(record.exc_info) if record.exc_info else None
        extra["message"] = message
        return extra
