"""Layovers of an itinerary and the warnings worth showing next to a flight.

Computed from the stored segments (`price_history.details`), so they apply to every price
that has flight details, old ones included. Segment times are local airport times; a layover
stays in one city, so the difference of arrival and next departure is its real length.
"""

from dataclasses import dataclass
from datetime import date, datetime

# Below this a missed connection is likely (Google Flights shows no such warning).
SHORT_MINUTES = 60
# Changing airports (e.g. Heathrow → Gatwick) needs a transfer across the city.
SHORT_AIRPORT_CHANGE_MINUTES = 180
# Google Flights marks layovers from about 5 hours as long.
LONG_MINUTES = 5 * 60
# Landing after midnight (00:00–03:59) and waiting 3+ hours until morning (05:00+) is also an
# overnight airport wait even though both segments share the same calendar date.
POST_MIDNIGHT_HOUR = 4
MORNING_HOUR = 5
POST_MIDNIGHT_MIN_MINUTES = 180

AIRPORT_CHANGE = "airport_change"
SHORT = "short"
LONG = "long"
OVERNIGHT = "overnight"
# Order in which warnings are shown: the ones that can cost the connection first.
WARNINGS = (AIRPORT_CHANGE, SHORT, OVERNIGHT, LONG)


@dataclass(frozen=True)
class Layover:
    arrival_airport: str
    departure_airport: str
    minutes: int | None
    # The next flight leaves on a later (local) day than the previous one arrived.
    overnight: bool

    @property
    def airport_change(self) -> bool:
        return self.arrival_airport != self.departure_airport

    @property
    def warnings(self) -> tuple[str, ...]:
        found = set()
        if self.airport_change:
            found.add(AIRPORT_CHANGE)
        if self.minutes is not None:
            limit = SHORT_AIRPORT_CHANGE_MINUTES if self.airport_change else SHORT_MINUTES
            if self.minutes < limit:
                found.add(SHORT)
            if self.minutes >= LONG_MINUTES:
                found.add(LONG)
        if self.overnight:
            found.add(OVERNIGHT)
        return tuple(warning for warning in WARNINGS if warning in found)


def _moment(day: str | None, time: str | None) -> datetime | None:
    if not day or not time:
        return None
    try:
        return datetime.fromisoformat(f"{day}T{time}")
    except ValueError:
        return None


def _day(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _is_overnight(
    arriving: dict,
    leaving: dict,
    arrival: datetime | None,
    departure: datetime | None,
    minutes: int | None,
) -> bool:
    arrival_day = arrival.date() if arrival else _day(arriving.get("arrival_date"))
    departure_day = departure.date() if departure else _day(leaving.get("departure_date"))
    if arrival_day and departure_day and departure_day > arrival_day:
        return True
    return bool(
        arrival
        and departure
        and minutes is not None
        and arrival.hour < POST_MIDNIGHT_HOUR
        and departure.hour >= MORNING_HOUR
        and minutes >= POST_MIDNIGHT_MIN_MINUTES
    )


def layovers(segments: list[dict]) -> list[Layover]:
    """One layover between each two consecutive segments."""
    result = []
    for arriving, leaving in zip(segments, segments[1:], strict=False):
        arrival = _moment(arriving.get("arrival_date"), arriving.get("arrival_time"))
        departure = _moment(leaving.get("departure_date"), leaving.get("departure_time"))
        minutes = (
            int((departure - arrival).total_seconds() // 60) if arrival and departure else None
        )
        result.append(
            Layover(
                arrival_airport=arriving.get("to") or "?",
                departure_airport=leaving.get("from") or arriving.get("to") or "?",
                minutes=minutes,
                overnight=_is_overnight(arriving, leaving, arrival, departure, minutes),
            )
        )
    return result


def itinerary_warnings(details: dict | None) -> tuple[str, ...]:
    """Every warning of the outbound and (if loaded) the return flight, each once."""
    found = set()
    for part in (details or {}, (details or {}).get("return") or {}):
        for layover in layovers(part.get("segments") or []):
            found.update(layover.warnings)
    return tuple(warning for warning in WARNINGS if warning in found)


def has_overnight_layover(details: dict | None) -> bool:
    """True when the outbound or (if loaded) the return flight has an overnight layover."""
    return OVERNIGHT in itinerary_warnings(details)


def allowed_observation(
    details: dict | None, return_date: date | None, *, no_overnight_layovers: bool
) -> bool:
    """Whether a stored observation satisfies the overnight-layover filter.

    For round trips with flight segments, a missing `return` itinerary means the return leg
    was never checked and could be an overnight layover.
    """
    if not no_overnight_layovers:
        return True
    if has_overnight_layover(details):
        return False
    if (
        return_date is not None
        and (details or {}).get("segments")
        and "return" not in (details or {})
    ):
        return False
    return True
