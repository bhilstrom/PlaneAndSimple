"""Fallback: fares from the Google Flights result page (only the ~10 flights it shows without
"more flights"). fast-flights (pinned in pyproject.toml) builds the outbound page URL; return
pages pin the chosen outbound legs in `tfs`."""

import base64
import json
import re
from dataclasses import replace
from urllib.parse import urlencode

from flighttracker.providers.base import ProviderError
from flighttracker.providers.google_flights.common import (
    CONSENT_COOKIE,
    URL,
    FareOption,
    FareRequest,
    SelectedLeg,
    fare_options,
    http_client,
)

_RESULTS_SCRIPT = re.compile(r'<script[^>]*class="ds:1"[^>]*>(.*?)</script>', re.S)
_PAGE_SEAT = {"economy": 1, "premium-economy": 2, "business": 3, "first": 4}


def parse_fares(html: str) -> list[FareOption]:
    """Price, stops and airline of every priced itinerary on a Google Flights result page.

    Our own reading of the page data instead of fast-flights' parser, which only reads the
    "other flights" list (missing the "best flights", often the only direct ones) and crashes
    on empty result lists or itineraries without a price. Raises ValueError if the page does
    not have the expected structure.
    """
    match = _RESULTS_SCRIPT.search(html)
    if match is None:
        raise ValueError("result data missing")
    data = match.group(1).split("data:", 1)[1].rsplit(",", 1)[0]
    if data.endswith("errorHasStatus: true"):
        return []  # Google's "no flights found"
    return fare_options(json.loads(data))


def _page_query(request: FareRequest):
    """The only place that touches the `fast-flights` API (pinned in pyproject.toml)."""
    try:
        from fast_flights import FlightQuery, Passengers, create_query
    except ImportError as exc:
        raise ProviderError("Package fast-flights missing: pip install '.[scraper]'") from exc

    legs = [
        FlightQuery(
            date=request.departure.isoformat(),
            from_airport=request.origin,
            to_airport=request.destination,
        )
    ]
    if request.return_date is not None:
        legs.append(
            FlightQuery(
                date=request.return_date.isoformat(),
                from_airport=request.destination,
                to_airport=request.origin,
            )
        )
    return create_query(
        flights=legs,
        seat=request.seat,
        trip="round-trip" if request.return_date is not None else "one-way",
        passengers=Passengers(adults=request.adults, children=request.children),
        currency=request.currency,
        max_stops=request.max_stops,
    )


def _varint(value: int) -> bytes:
    out = bytearray()
    while value > 0x7F:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value)
    return bytes(out)


def _varint_field(field: int, value: int) -> bytes:
    return _varint(field << 3) + _varint(value)


def _bytes_field(field: int, data: bytes) -> bytes:
    return _varint((field << 3) | 2) + _varint(len(data)) + data


def _str_field(field: int, text: str) -> bytes:
    return _bytes_field(field, text.encode())


def _flight_data(
    origin: str,
    destination: str,
    day: str,
    max_stops: int | None,
    selected: list[SelectedLeg] | None = None,
) -> bytes:
    buf = _str_field(2, day)
    for leg_origin, leg_day, leg_dest, airline, number in selected or ():
        leg = (
            _str_field(1, leg_origin)
            + _str_field(2, leg_day)
            + _str_field(3, leg_dest)
            + _str_field(5, airline)
            + _str_field(6, number)
        )
        buf += _bytes_field(4, leg)
    if max_stops is not None and max_stops < 3:
        buf += _varint_field(5, max_stops)
    buf += _bytes_field(13, _varint_field(1, 1) + _str_field(2, origin))
    buf += _bytes_field(14, _varint_field(1, 1) + _str_field(2, destination))
    return buf


def return_tfs(request: FareRequest, outbound: list[SelectedLeg]) -> str:
    """`tfs` query parameter for the return-flight page of a chosen outbound itinerary."""
    if request.return_date is None:
        raise ValueError("return_tfs requires return_date")
    buf = _varint_field(1, 28) + _varint_field(2, 2)
    buf += _bytes_field(
        3,
        _flight_data(
            request.origin,
            request.destination,
            request.departure.isoformat(),
            request.max_stops,
            outbound,
        ),
    )
    buf += _bytes_field(
        3,
        _flight_data(
            request.destination,
            request.origin,
            request.return_date.isoformat(),
            request.max_stops,
        ),
    )
    for _ in range(request.adults):
        buf += _varint_field(8, 1)
    for _ in range(request.children):
        buf += _varint_field(8, 2)
    buf += _varint_field(9, _PAGE_SEAT[request.seat])
    buf += _varint_field(14, 1) + _varint_field(19, 1)
    return base64.urlsafe_b64encode(buf).decode("ascii").rstrip("=")


def with_url(options: list[FareOption], request: FareRequest) -> list[FareOption]:
    """Link to exactly this search on Google Flights (for checking and booking)."""
    url = f"{URL}?{urlencode(_page_query(request).params())}"
    return [replace(o, details={**o.details, "url": url}) if o.details else o for o in options]


def _fetch_page(params: dict[str, str]) -> list[FareOption]:
    try:
        html = http_client().get(URL, params=params, headers={"Cookie": CONSENT_COOKIE}).text
        if "<title>Before you continue" in html:
            raise ProviderError("Google Flights shows its cookie consent page instead of results")
        return parse_fares(html)
    except ProviderError:
        raise
    except Exception as exc:
        raise ProviderError(f"Google Flights query failed ({type(exc).__name__})") from None


def fast_flights_search(request: FareRequest) -> list[FareOption]:
    """Fares from the result page (only the flights it shows without "more flights")."""
    query = _page_query(request)
    return with_url(_fetch_page(query.params()), request)


def fast_flights_return_search(
    request: FareRequest, outbound: list[SelectedLeg]
) -> list[FareOption]:
    """Return fares for the given outbound flights from the Google Flights result page."""
    if request.return_date is None:
        return []
    params = {"tfs": return_tfs(request, outbound), "hl": "en", "curr": request.currency}
    return _fetch_page(params)
