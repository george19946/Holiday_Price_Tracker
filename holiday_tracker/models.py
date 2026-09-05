"""Core data types shared across the catalog, providers, engine, and storage layers.

These are deliberately plain and I/O-free: constructing one never touches the
network, the filesystem, or the database. That keeps the models trivially
testable and reusable everywhere (CLI parsing, engine internals, SQLite
(de)serialisation) without import cycles.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from functools import total_ordering

from pydantic import BaseModel, Field, field_validator, model_validator

# --------------------------------------------------------------------------
# Small shared enums
# --------------------------------------------------------------------------


class Weekday(StrEnum):
    """ISO weekday, spelled out for readable CLI flags and YAML/JSON."""

    mon = "mon"
    tue = "tue"
    wed = "wed"
    thu = "thu"
    fri = "fri"
    sat = "sat"
    sun = "sun"


# date.isoweekday() -> 1 (Mon) .. 7 (Sun)
ISO_WEEKDAY_TO_ENUM: dict[int, Weekday] = {
    1: Weekday.mon,
    2: Weekday.tue,
    3: Weekday.wed,
    4: Weekday.thu,
    5: Weekday.fri,
    6: Weekday.sat,
    7: Weekday.sun,
}


class SpendStyle(StrEnum):
    """How much a traveller expects to spend per day, relative to the bundled
    "normal" daily-spend estimate for a city (see catalog/spend.yaml)."""

    thrifty = "thrifty"
    normal = "normal"
    comfortable = "comfortable"


# Multiplier applied to a city's bundled "normal" daily spend estimate.
SPEND_STYLE_MULTIPLIERS: dict[SpendStyle, float] = {
    SpendStyle.thrifty: 0.7,
    SpendStyle.normal: 1.0,
    SpendStyle.comfortable: 1.6,
}


_CURRENCY_SYMBOLS = {"GBP": "£", "EUR": "€", "USD": "$"}


# --------------------------------------------------------------------------
# Money
# --------------------------------------------------------------------------


@total_ordering
class Money(BaseModel):
    """An amount of currency, stored as integer minor units (e.g. pence) so
    totals built up from many components never drift the way running a
    budget in floating-point pounds would.

    Arithmetic between two Money values requires matching currencies —
    converting currencies is an explicit, separate step (left to the
    pricing engine, which documents its FX approach), never an implicit
    side effect of addition.
    """

    model_config = {"frozen": True}

    minor_units: int
    currency: str = "GBP"

    @classmethod
    def from_major(cls, amount: float, currency: str = "GBP") -> Money:
        """Build from a "normal" decimal amount, e.g. Money.from_major(12.5, "GBP")."""
        return cls(minor_units=round(amount * 100), currency=currency)

    @classmethod
    def zero(cls, currency: str = "GBP") -> Money:
        return cls(minor_units=0, currency=currency)

    @property
    def amount(self) -> float:
        """The value as a decimal major-unit amount, e.g. 12.50."""
        return self.minor_units / 100

    def _require_same_currency(self, other: Money) -> None:
        if self.currency != other.currency:
            raise ValueError(f"currency mismatch: {self.currency} vs {other.currency}")

    def __add__(self, other: Money) -> Money:
        self._require_same_currency(other)
        return Money(minor_units=self.minor_units + other.minor_units, currency=self.currency)

    def __sub__(self, other: Money) -> Money:
        self._require_same_currency(other)
        return Money(minor_units=self.minor_units - other.minor_units, currency=self.currency)

    def __mul__(self, factor: float) -> Money:
        return Money(minor_units=round(self.minor_units * factor), currency=self.currency)

    __rmul__ = __mul__

    def __lt__(self, other: Money) -> bool:
        self._require_same_currency(other)
        return self.minor_units < other.minor_units

    def __str__(self) -> str:
        symbol = _CURRENCY_SYMBOLS.get(self.currency, f"{self.currency} ")
        return f"{symbol}{self.amount:,.2f}"


# --------------------------------------------------------------------------
# Date rule
# --------------------------------------------------------------------------


class DateRule(BaseModel):
    """A declarative description of "which dates are acceptable" — e.g. "any
    Thursday to Sunday in 2027". Expanded into concrete (depart, return) date
    pairs by dates.expand_date_pairs(); this model only validates the rule
    itself, it does no date arithmetic.
    """

    window_start: date
    window_end: date
    depart_dow: set[Weekday] = Field(default_factory=set)  # empty = any day
    return_dow: set[Weekday] = Field(default_factory=set)  # empty = any day
    nights_min: int = 1
    nights_max: int = 1
    # Inclusive (start, end) ranges to avoid entirely; a trip overlapping any
    # blackout range on any day is excluded. A bare date is normalised to a
    # one-day range by the validator below.
    blackouts: list[tuple[date, date]] = Field(default_factory=list)
    months: set[int] | None = None  # restrict departure month to 1..12

    @field_validator("blackouts", mode="before")
    @classmethod
    def _normalize_blackouts(cls, value: object) -> list[tuple[date, date]]:
        if value is None:
            return []
        normalized: list[tuple[date, date]] = []
        for item in value:  # type: ignore[union-attr]
            if isinstance(item, list | tuple):
                start, end = item
            else:
                start = end = item
            normalized.append((start, end))
        return normalized

    @model_validator(mode="after")
    def _validate(self) -> DateRule:
        if self.window_start > self.window_end:
            raise ValueError("window_start must be on or before window_end")
        if self.nights_min < 1:
            raise ValueError("nights_min must be >= 1")
        if self.nights_max < self.nights_min:
            raise ValueError("nights_max must be >= nights_min")
        if self.months is not None and not all(1 <= m <= 12 for m in self.months):
            raise ValueError("months must each be in 1..12")
        for start, end in self.blackouts:
            if start > end:
                raise ValueError(f"blackout range {start}..{end} has start after end")
        return self


# --------------------------------------------------------------------------
# Stay filters
# --------------------------------------------------------------------------


class StayFilters(BaseModel):
    """Accommodation constraints, asked as a quick yes/no list in the wizard."""

    exclude_hostels: bool = False
    min_rating: float | None = None  # Hotellook rating scale, 0-10
    max_centre_km: float | None = None
    free_cancellation_only: bool = False

    @field_validator("min_rating")
    @classmethod
    def _check_rating(cls, value: float | None) -> float | None:
        if value is not None and not (0 <= value <= 10):
            raise ValueError("min_rating must be between 0 and 10")
        return value

    @field_validator("max_centre_km")
    @classmethod
    def _check_distance(cls, value: float | None) -> float | None:
        if value is not None and value <= 0:
            raise ValueError("max_centre_km must be positive")
        return value


# --------------------------------------------------------------------------
# Search specification
# --------------------------------------------------------------------------


class SearchSpec(BaseModel):
    """Everything needed to run one search: where the traveller is willing to
    fly from, where they want to go, when, for how much, for how many people,
    at what spending style, and under what accommodation constraints.

    `destination` is a free-form string (a city name/id or a region name/id)
    resolved against the bundled catalogue by catalog.loader.resolve_destination —
    kept as a string here so a SearchSpec can be built and validated before
    the catalogue is even loaded.
    """

    origins: list[str]  # IATA airport codes the traveller is willing to fly from
    destination: str
    date_rule: DateRule
    budget: Money
    party_size: int = 1
    occupancy_per_room: int = 2
    spend_style: SpendStyle = SpendStyle.normal
    stay_filters: StayFilters = Field(default_factory=StayFilters)

    @field_validator("origins")
    @classmethod
    def _normalize_origins(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("at least one origin airport is required")
        return [code.strip().upper() for code in value]

    @field_validator("destination")
    @classmethod
    def _check_destination(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("destination must not be empty")
        return value

    @field_validator("party_size", "occupancy_per_room")
    @classmethod
    def _check_positive(cls, value: int) -> int:
        if value < 1:
            raise ValueError("must be >= 1")
        return value

    @property
    def rooms_needed(self) -> int:
        """Ceiling division: party_size guests split into occupancy_per_room-sized rooms."""
        return -(-self.party_size // self.occupancy_per_room)


# --------------------------------------------------------------------------
# Provider-facing quotes and engine-facing results
#
# These are deliberately loose (Optional fields, a free-text `source`) because
# they describe data shaped by external, free-tier providers whose coverage
# is uneven — see providers/ and the project README's "honest limitations".
# --------------------------------------------------------------------------


class FlightQuote(BaseModel):
    """One priced flight for a specific origin/destination/date combination,
    as observed from a provider at a point in time."""

    origin: str
    destination_iata: str
    depart_date: date
    return_date: date
    price: Money
    observed_at: datetime
    source: str
    deep_link: str | None = None


class StayQuote(BaseModel):
    """One priced stay for a specific city and date range."""

    city_id: str
    check_in: date
    check_out: date
    nightly_rate: Money
    property_type: str
    rating: float | None = None
    distance_km: float | None = None
    free_cancellation: bool = False
    observed_at: datetime
    source: str
    deep_link: str | None = None
    # "observed": a real cached price for this city/date range.
    # "city_median_estimate": coverage was too thin, fell back to a city-level median.
    confidence: str = "observed"


class Candidate(BaseModel):
    """One concrete (origin, destination city, depart, return) combination
    with its flight price attached — the unit of work produced by the Stage 1
    flight sweep and consumed by Stage 2 stay pricing."""

    origin: str
    destination_city_id: str
    destination_iata: str
    depart_date: date
    return_date: date
    nights: int
    flight: FlightQuote


class Package(BaseModel):
    """A fully priced holiday: flights + accommodation + estimated spend for
    the whole party, for one concrete destination and date pair — the unit of
    work produced by Stage 3 (assemble, rank, annotate)."""

    destination_city_id: str
    origin: str
    depart_date: date
    return_date: date
    nights: int
    party_size: int
    flights_cost: Money
    accommodation_cost: Money
    spend_cost: Money
    total_cost: Money
    flight_deep_link: str | None = None
    stay: StayQuote | None = None
    fits_budget: bool = False
    over_budget_by: Money | None = None

    @property
    def breakdown(self) -> dict[str, Money]:
        return {
            "flights": self.flights_cost,
            "accommodation": self.accommodation_cost,
            "spend": self.spend_cost,
            "total": self.total_cost,
        }
