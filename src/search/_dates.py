"""Shared date handling for the retrieval service.

A single home for how the search layer interprets date bounds, so the semantic
and structured paths stay consistent (notably the inclusive end-of-day rule for
upper bounds).
"""
from datetime import datetime
from typing import Union

DateLike = Union[datetime, str]


def coerce_datetime(value: DateLike, *, end_of_day: bool = False) -> datetime:
    """Normalise a date/datetime input to a ``datetime``.

    Bare ``YYYY-MM-DD`` strings (or datetimes with no time component) pin to the
    start of the day, or to ``23:59:59.999999`` when ``end_of_day`` is set — so an
    inclusive upper bound covers that whole day rather than excluding its
    afternoon sittings.
    """
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if end_of_day and value.time() == datetime.min.time():
        return value.replace(hour=23, minute=59, second=59, microsecond=999999)
    return value
