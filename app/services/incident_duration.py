from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

from app.services.timezone_service import APP_TIMEZONE, local_now

FINAL_STATUS_KEYS = {"encerrado", "falso positivo"}


@dataclass(frozen=True)
class IncidentDuration:
    label: str
    days: int | None
    status: str
    start_date: date | None = None
    end_date: date | None = None
    reference: str = "start_date"

    @property
    def is_valid(self) -> bool:
        return self.status == "valid"


def normalize_status_key(value: Any) -> str:
    text = str(value or "").strip().casefold()
    return " ".join(text.split())


def is_final_status(value: Any) -> bool:
    return normalize_status_key(value) in FINAL_STATUS_KEYS


def to_local_date(value: Any, *, assume_naive_local: bool = True) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        source = value
        if source.tzinfo is None:
            source = source.replace(tzinfo=APP_TIMEZONE if assume_naive_local else timezone.utc)
        return source.astimezone(APP_TIMEZONE).date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    for parser in (datetime.fromisoformat,):
        try:
            parsed = parser(text)
            return to_local_date(parsed, assume_naive_local=assume_naive_local)
        except ValueError:
            continue
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def format_days(days: int) -> str:
    return "1 dia" if days == 1 else f"{days} dias"


def calculate_incident_duration(
    *,
    start_date: Any,
    end_date: Any = None,
    status: Any = None,
    created_at: Any = None,
    today: date | None = None,
) -> IncidentDuration:
    start = to_local_date(start_date) or to_local_date(created_at)
    reference = "start_date" if to_local_date(start_date) else "created_at"
    if not start:
        return IncidentDuration(label="Não informado", days=None, status="missing_start", reference="none")

    end = to_local_date(end_date)
    if end is None:
        end = today or local_now().date()
        end_reference = "today"
    else:
        end_reference = "end_date"

    if end < start:
        return IncidentDuration(
            label="Data inconsistente",
            days=None,
            status="inconsistent",
            start_date=start,
            end_date=end,
            reference=f"{reference}->{end_reference}",
        )

    days = (end - start).days
    return IncidentDuration(
        label=format_days(days),
        days=days,
        status="valid",
        start_date=start,
        end_date=end,
        reference=f"{reference}->{end_reference}",
    )


def duration_for_incident(incident: Any, *, today: date | None = None) -> IncidentDuration:
    return calculate_incident_duration(
        start_date=getattr(incident, "start_date", None),
        end_date=getattr(incident, "end_date", None),
        status=getattr(incident, "status_incident", None),
        created_at=getattr(incident, "created_at", None),
        today=today,
    )
