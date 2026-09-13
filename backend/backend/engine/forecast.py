"""
90-day financial forecasting.

This module reconstructs the user's expected cash position by combining:

1. Current available balance
2. Confirmed/scheduled future events
3. Recurring patterns inferred from historical events
4. Minimum balance requirement
5. Optional spending-change scenarios

This module does NOT decide whether the user should buy something.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable

from .events import (
    event_effective_date,
    is_eligible_for_forecast,
    project_recurring_events,
)
from .models import FinancialEvent, UserProfile
from .state import ExchangeRateTable


# ---------------------------------------------------------------------------
# DATA MODELS
# ---------------------------------------------------------------------------

@dataclass
class ForecastDay:
    """Projected financial state for one day."""

    date: date
    starting_balance: float
    income: float
    expenses: float
    ending_balance: float


@dataclass
class FinancialForecast:
    """Complete forecast over a date range."""

    start_date: date
    end_date: date
    days: list[ForecastDay]
    minimum_balance: float

    @property
    def ending_balance(self) -> float:
        if not self.days:
            return 0.0

        return self.days[-1].ending_balance

    @property
    def minimum_projected_balance(self) -> float:
        if not self.days:
            return 0.0

        return min(
            day.ending_balance
            for day in self.days
        )

    @property
    def is_safe(self) -> bool:
        return (
            self.minimum_projected_balance
            >= self.minimum_balance
        )

    @property
    def first_unsafe_date(self) -> date | None:
        for day in self.days:
            if day.ending_balance < self.minimum_balance:
                return day.date

        return None

    def day(self, target_date: date) -> ForecastDay | None:
        for day in self.days:
            if day.date == target_date:
                return day

        return None


# ---------------------------------------------------------------------------
# EVENT NORMALIZATION
# ---------------------------------------------------------------------------

def _event_home_amount(
    event: FinancialEvent,
    exchange_rates: ExchangeRateTable,
    home_currency: str,
) -> float:
    """
    Convert an event amount into the user's home currency.

    Same-currency events require no conversion.
    """

    if event.amount is None:
        return 0.0

    return exchange_rates.convert(
        amount=event.amount,
        from_currency=event.currency,
        to_currency=home_currency,
        rate_date=event.event_date,
    )


# ---------------------------------------------------------------------------
# EVENT MAP
# ---------------------------------------------------------------------------

def _build_event_map(
    events: Iterable[FinancialEvent],
    start_date: date,
    end_date: date,
    exchange_rates: ExchangeRateTable,
    home_currency: str,
):
    """
    Build:

        effective_date -> (income, expenses)

    for explicit known events.
    """

    event_map: dict[
        date,
        tuple[float, float],
    ] = {}

    for event in events:

        if not is_eligible_for_forecast(event):
            continue

        effective_date = event_effective_date(event)

        if not (
            start_date
            <= effective_date
            <= end_date
        ):
            continue

        amount = _event_home_amount(
            event,
            exchange_rates,
            home_currency,
        )

        income, expenses = event_map.get(
            effective_date,
            (0.0, 0.0),
        )

        if event.direction.strip().lower() in {
            "credit",
            "income",
        }:
            income += amount

        elif event.direction.strip().lower() in {
            "debit",
            "expense",
        }:
            expenses += amount

        event_map[effective_date] = (
            income,
            expenses,
        )

    return event_map


# ---------------------------------------------------------------------------
# RECURRING EVENTS
# ---------------------------------------------------------------------------

def _add_recurring_events(
    event_map,
    events: Iterable[FinancialEvent],
    start_date: date,
    end_date: date,
    exchange_rates: ExchangeRateTable,
    home_currency: str,
):
    """
    Add inferred recurring events to the explicit event map.

    Historical events are used only to infer future occurrences.
    """

    projected = project_recurring_events(
        events,
        start_date,
        end_date,
    )

    for source_event, projected_date in projected:

        # An explicit future event is authoritative. Do not add an
        # inferred recurrence on the same date (or we double-count it).
        if projected_date in event_map:
            continue

        explicit_income = 0.0
        explicit_expenses = 0.0

        amount = _event_home_amount(
            source_event,
            exchange_rates,
            home_currency,
        )

        direction = (
            source_event.direction
            or ""
        ).strip().lower()

        if direction in {
            "credit",
            "income",
        }:
            explicit_income += amount

        elif direction in {
            "debit",
            "expense",
        }:
            explicit_expenses += amount

        event_map[projected_date] = (
            explicit_income,
            explicit_expenses,
        )

    return event_map


# ---------------------------------------------------------------------------
# FORECAST
# ---------------------------------------------------------------------------

def forecast_timeline(
    profile: UserProfile,
    events: Iterable[FinancialEvent],
    start_date: date,
    end_date: date,
    exchange_rates: ExchangeRateTable,
) -> FinancialForecast:
    """
    Forecast the user's balance between start_date and end_date.

    The starting balance is the profile's current available balance.

    Explicit eligible events and inferred recurring events are applied
    chronologically.
    """

    if end_date < start_date:
        raise ValueError(
            "end_date cannot be before start_date"
        )

    events = list(events)

    event_map = _build_event_map(
        events=events,
        start_date=start_date,
        end_date=end_date,
        exchange_rates=exchange_rates,
        home_currency=profile.home_currency,
    )

    event_map = _add_recurring_events(
        event_map=event_map,
        events=events,
        start_date=start_date,
        end_date=end_date,
        exchange_rates=exchange_rates,
        home_currency=profile.home_currency,
    )

    days: list[ForecastDay] = []

    balance = float(
        profile.current_available_balance
    )

    current_date = start_date

    while current_date <= end_date:

        starting_balance = balance

        income, expenses = event_map.get(
            current_date,
            (0.0, 0.0),
        )

        balance = (
            starting_balance
            + income
            - expenses
        )

        days.append(
            ForecastDay(
                date=current_date,
                starting_balance=starting_balance,
                income=income,
                expenses=expenses,
                ending_balance=balance,
            )
        )

        current_date += timedelta(days=1)

    return FinancialForecast(
        start_date=start_date,
        end_date=end_date,
        days=days,
        minimum_balance=float(
            profile.minimum_balance_to_keep
        ),
    )


def forecast_next_90_days(
    profile: UserProfile,
    events: Iterable[FinancialEvent],
    start_date: date,
    exchange_rates: ExchangeRateTable,
) -> FinancialForecast:
    """
    Forecast the next 90 days starting on start_date.

    The challenge safety window is inclusive of the request date.
    """

    end_date = start_date + timedelta(days=89)

    return forecast_timeline(
        profile=profile,
        events=events,
        start_date=start_date,
        end_date=end_date,
        exchange_rates=exchange_rates,
    )


# ---------------------------------------------------------------------------
# QUERY HELPERS
# ---------------------------------------------------------------------------

def projected_balance_on(
    forecast: FinancialForecast,
    target_date: date,
) -> float | None:
    """Return projected ending balance on a specific date."""

    day = forecast.day(target_date)

    if day is None:
        return None

    return day.ending_balance


def remains_above_minimum(
    forecast: FinancialForecast,
) -> bool:
    """Whether the complete forecast stays above the reserve."""

    return forecast.is_safe


def summarize_forecast(
    forecast: FinancialForecast,
) -> dict:
    """
    Produce a compact summary suitable for the decision layer or API.
    """

    return {
        "start_date": forecast.start_date.isoformat(),
        "end_date": forecast.end_date.isoformat(),
        "ending_balance": round(
            forecast.ending_balance,
            2,
        ),
        "minimum_projected_balance": round(
            forecast.minimum_projected_balance,
            2,
        ),
        "minimum_balance_required": round(
            forecast.minimum_balance,
            2,
        ),
        "is_safe": forecast.is_safe,
        "first_unsafe_date": (
            forecast.first_unsafe_date.isoformat()
            if forecast.first_unsafe_date
            else None
        ),
    }