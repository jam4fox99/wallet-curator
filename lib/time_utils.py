from datetime import date, datetime, time, timedelta, timezone


UTC = timezone.utc


def now_utc() -> datetime:
    return datetime.now(UTC)


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def parse_db_timestamp(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return ensure_utc(value)
    text = str(value).strip()
    if not text:
        return None
    text = text.replace("Z", "+00:00")
    try:
        return ensure_utc(datetime.fromisoformat(text))
    except ValueError:
        pass

    for fmt in (
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
        "%m/%d/%Y %H:%M:%S",
        "%m/%d/%Y %I:%M:%S %p",
    ):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    raise ValueError(f"Unsupported timestamp format: {value}")


def to_db_timestamp(value) -> str:
    dt = parse_db_timestamp(value) if not isinstance(value, datetime) else ensure_utc(value)
    return dt.isoformat()


def day_bounds(day_value):
    if isinstance(day_value, datetime):
        day_value = ensure_utc(day_value).date()
    start = datetime.combine(day_value, time.min, tzinfo=UTC)
    end = start + timedelta(days=1) - timedelta(microseconds=1)
    return start, end


def iter_days(start_day: date, end_day: date):
    current = start_day
    while current <= end_day:
        yield current
        current += timedelta(days=1)
