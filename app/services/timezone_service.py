from datetime import date, datetime, time, timezone
import os
from zoneinfo import ZoneInfo


APP_TIMEZONE_NAME = os.getenv("TIMEZONE", "America/Sao_Paulo")
APP_TIMEZONE = ZoneInfo(APP_TIMEZONE_NAME)


def utc_now():
    return datetime.now(timezone.utc)


def local_now():
    return utc_now().astimezone(APP_TIMEZONE)


def local_naive_now():
    return local_now().replace(tzinfo=None, microsecond=0)


def combine_local_date_with_current_time(value):
    current = local_now()
    return datetime.combine(value, current.time()).replace(microsecond=0, tzinfo=None)


def to_local(value, *, assume_naive_utc=True):
    if not value:
        return None
    source = value
    if source.tzinfo is None:
        source = source.replace(tzinfo=timezone.utc if assume_naive_utc else APP_TIMEZONE)
    return source.astimezone(APP_TIMEZONE)


def format_local_datetime(value):
    local_value = to_local(value)
    return local_value.strftime("%d/%m/%Y %H:%M:%S") if local_value else ""


def local_date_bounds_as_utc_naive(start_date, end_date=None):
    start_local = datetime.combine(start_date, time.min, tzinfo=APP_TIMEZONE)
    end_source = end_date or start_date
    end_local = datetime.combine(end_source, time.max, tzinfo=APP_TIMEZONE)
    return (
        start_local.astimezone(timezone.utc).replace(tzinfo=None),
        end_local.astimezone(timezone.utc).replace(tzinfo=None),
    )


def parse_iso_date(value):
    if not value:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    return datetime.strptime(str(value), "%Y-%m-%d").date()
