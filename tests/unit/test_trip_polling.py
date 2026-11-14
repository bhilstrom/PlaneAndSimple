from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace

from starlette.datastructures import FormData

from flighttracker.domain.filters import CabinClass
from flighttracker.domain.trips import LayoverRule
from flighttracker.providers.base import PriceQuote
from flighttracker.services.ingestion import accept_quote
from flighttracker.services.trip_polling import connect_quotes, trip_query
from flighttracker.web.trip_forms import (
    default_trip_values,
    trip_input_from_values,
    trip_values_from_form,
)


def quote(origin, destination, day, departure, arrival, price="100", currency="CHF"):
    return SimpleNamespace(
        origin=origin,
        destination=destination,
        currency=currency,
        departure_date=date.fromisoformat(day),
        price=Decimal(price),
        details={"departure_time": departure, "arrival_date": day, "arrival_time": arrival},
    )


def test_one_path_per_last_flight_and_no_cap():
    # 1200 first-leg flights all connect to the same two second-leg flights.
    firsts = [quote("ZRH", "LIS", "2027-05-10", "06:00", "08:00", str(i)) for i in range(1200)]
    seconds = [
        quote("LIS", "OPO", "2027-05-12", "08:00", "09:00"),
        quote("LIS", "OPO", "2027-05-13", "08:00", "09:00"),
    ]
    connected, paths = connect_quotes(
        [(f,) for f in firsts],
        seconds,
        LayoverRule(2, 3),
        final_leg=False,
        ends_on=date(2027, 5, 20),
    )
    assert connected == seconds
    assert [
        path[-1] for path in paths
    ] == seconds  # 2 states instead of 2400 (formerly cut at 1000)


def test_trip_form_and_polling_propagate_no_overnight_layovers():
    settings = SimpleNamespace(
        default_poll_interval_minutes=1440,
        default_currency="USD",
        scraper_no_overnight_layovers=True,
    )
    assert default_trip_values(settings)["no_overnight_layovers"] is True
    posted = FormData(
        [
            ("name", "Loop"),
            ("starts_on", "2027-05-01"),
            ("ends_on", "2027-05-10"),
            ("adults", "1"),
            ("children", "0"),
            ("cabin_classes", "economy"),
            ("max_stops", "any"),
            ("no_overnight_layovers", "1"),
            ("leg_origin", "ZRH"),
            ("leg_destination", "LIS"),
            ("min_layover_days", ""),
            ("max_layover_days", ""),
            ("leg_origin", "LIS"),
            ("leg_destination", "ZRH"),
            ("min_layover_days", "2"),
            ("max_layover_days", "4"),
        ]
    )
    trip_in = trip_input_from_values(trip_values_from_form(posted, settings), settings)
    assert trip_in.no_overnight_layovers is True

    search = SimpleNamespace(filters={"trip_type": "one_way", "no_overnight_layovers": True})
    q = trip_query(
        search, CabinClass.ECONOMY, date(2027, 5, 1), (date(2027, 5, 2),), "ZRH", "LIS", "CHF"
    )
    assert q.no_overnight_layovers is True

    overnight_quote = PriceQuote(
        provider="google_flights",
        origin="ZRH",
        destination="LIS",
        departure_date=date(2027, 5, 2),
        return_date=None,
        cabin_class=CabinClass.ECONOMY,
        stops=1,
        price=Decimal("99"),
        currency="CHF",
        observed_at=datetime(2027, 4, 1, tzinfo=UTC),
        details={
            "segments": [
                {
                    "from": "ZRH",
                    "to": "MAD",
                    "departure_date": "2027-05-02",
                    "departure_time": "21:00",
                    "arrival_date": "2027-05-02",
                    "arrival_time": "23:15",
                },
                {
                    "from": "MAD",
                    "to": "LIS",
                    "departure_date": "2027-05-03",
                    "departure_time": "07:00",
                    "arrival_date": "2027-05-03",
                    "arrival_time": "07:20",
                },
            ]
        },
    )
    assert not accept_quote(overnight_quote, q)
