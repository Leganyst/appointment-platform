from datetime import datetime, timezone

from google.protobuf.timestamp_pb2 import Timestamp


def to_datetime(ts: Timestamp | None) -> datetime | None:
    if ts is None:
        return None
    return ts.ToDatetime().replace(tzinfo=timezone.utc)


def _ensure_datetime(value) -> datetime | None:
    """Convert a variety of timestamp-like objects to aware datetime."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, Timestamp):
        return _ensure_datetime(value.ToDatetime())
    if hasattr(value, "to_pydatetime"):
        return _ensure_datetime(value.to_pydatetime())
    if hasattr(value, "to_datetime"):
        return _ensure_datetime(value.to_datetime())
    if hasattr(value, "timestamp"):
        return datetime.fromtimestamp(value.timestamp(), tz=timezone.utc)
    raise TypeError(f"Unsupported datetime type for to_timestamp: {type(value)!r}")


def to_timestamp(dt) -> Timestamp | None:
    dt = _ensure_datetime(dt)
    if dt is None:
        return None
    ts = Timestamp()
    ts.FromDatetime(dt)
    return ts
