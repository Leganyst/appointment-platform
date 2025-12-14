from datetime import datetime, timezone

from google.protobuf.timestamp_pb2 import Timestamp


def to_datetime(ts: Timestamp | None) -> datetime | None:
    if ts is None:
        return None
    return ts.ToDatetime().replace(tzinfo=timezone.utc)


def to_timestamp(dt: datetime | None) -> Timestamp | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    ts = Timestamp()
    ts.FromDatetime(dt)
    return ts
