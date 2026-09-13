"""
Financial state construction for Buy or Wait.

This module provides the normalized financial timeline used by the
forecasting and planning layers.

Important:
    This module does not make affordability decisions.
    It only organizes financial information into a form that later
    layers can reason about.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from .loader import Dataset, get_user_events
from .models import (
    ExchangeRate,
    FinancialEvent,
    FinancialMessage,
    UserProfile,
)


# ---------------------------------------------------------------------------
# NORMALIZED EVENT
# ---------------------------------------------------------------------------

@dataclass
class StateEvent:
    """
    A financial event expressed in the user's home currency.

    The original FinancialEvent is preserved so we never lose source data.
    """

    event: FinancialEvent

    amount_home_currency: Optional[float]

    is_income: bool
    is_expense: bool

    @property
    def event_date(self) -> date:
        return self.event.event_date

    @property
    def settlement_date(self) -> Optional[date]:
        return self.event.settlement_date

    @property
    def effective_date(self) -> date:
        """
        Date on which the event affects the financial timeline.

        If a settlement date exists, use it.
        Otherwise use the event date.
        """

        return self.event.settlement_date or self.event.event_date


# ---------------------------------------------------------------------------
# FINANCIAL TIMELINE
# ---------------------------------------------------------------------------

@dataclass
class FinancialTimeline:
    """
    Financial information for one user.

    This is intentionally a data representation, not a decision engine.
    """

    profile: UserProfile

    events: list[StateEvent] = field(default_factory=list)

    messages: list[FinancialMessage] = field(default_factory=list)

    def events_on_or_after(
        self,
        start_date: date,
    ) -> list[StateEvent]:
        """Return events occurring on or after start_date."""

        return [
            event
            for event in self.events
            if event.effective_date >= start_date
        ]

    def events_between(
        self,
        start_date: date,
        end_date: date,
    ) -> list[StateEvent]:
        """Return events within an inclusive date range."""

        return [
            event
            for event in self.events
            if start_date
            <= event.effective_date
            <= end_date
        ]

    def income_events(self) -> list[StateEvent]:
        """Return events classified as income."""

        return [
            event
            for event in self.events
            if event.is_income
        ]

    def expense_events(self) -> list[StateEvent]:
        """Return events classified as expenses."""

        return [
            event
            for event in self.events
            if event.is_expense
        ]


# ---------------------------------------------------------------------------
# EXCHANGE RATE LOOKUP
# ---------------------------------------------------------------------------

class ExchangeRateTable:
    """
    Fixed-date exchange rate lookup.

    The challenge uses dated exchange rates rather than live market data.

    We intentionally do NOT silently substitute a different date.
    """

    def __init__(
        self,
        rates: list[ExchangeRate],
    ) -> None:

        self._rates: dict[
            tuple[date, str, str],
            float,
        ] = {}

        for rate in rates:
            key = (
                rate.rate_date,
                rate.from_currency,
                rate.to_currency,
            )

            self._rates[key] = rate.rate

    def get_rate(
        self,
        rate_date: date,
        from_currency: str,
        to_currency: str,
    ) -> float:
        """
        Return the exact dated exchange rate.

        Same-currency conversion always has rate 1.
        """

        from_currency = from_currency.upper()
        to_currency = to_currency.upper()

        if from_currency == to_currency:
            return 1.0

        key = (
            rate_date,
            from_currency,
            to_currency,
        )

        try:
            return self._rates[key]
        except KeyError:
            raise ValueError(
                "No exchange rate available for "
                f"{from_currency} -> {to_currency} "
                f"on {rate_date.isoformat()}"
            )

    def convert(
        self,
        amount: float,
        rate_date: date,
        from_currency: str,
        to_currency: str,
    ) -> float:
        """Convert an amount using the exact dated rate."""

        rate = self.get_rate(
            rate_date=rate_date,
            from_currency=from_currency,
            to_currency=to_currency,
        )

        return amount * rate


# ---------------------------------------------------------------------------
# EVENT CLASSIFICATION
# ---------------------------------------------------------------------------

def _is_income(event: FinancialEvent) -> bool:
    """
    Determine whether an event represents money entering the account.

    Direction is the primary signal.
    """

    return event.direction.lower() in {
        "credit",
        "income",
    }


def _is_expense(event: FinancialEvent) -> bool:
    """
    Determine whether an event represents money leaving the account.

    Direction is the primary signal.
    """

    return event.direction.lower() in {
        "debit",
        "expense",
    }


# ---------------------------------------------------------------------------
# STATE EVENT CREATION
# ---------------------------------------------------------------------------

def normalize_event(
    event: FinancialEvent,
    profile: UserProfile,
    rates: ExchangeRateTable,
) -> StateEvent:
    """
    Convert a raw FinancialEvent into a StateEvent.

    The event amount remains None if it has not yet been recovered.
    """

    amount_home_currency: Optional[float] = None

    if event.amount is not None:

        amount_home_currency = rates.convert(
            amount=event.amount,
            rate_date=event.event_date,
            from_currency=event.currency,
            to_currency=profile.home_currency,
        )

    return StateEvent(
        event=event,
        amount_home_currency=amount_home_currency,
        is_income=_is_income(event),
        is_expense=_is_expense(event),
    )


# ---------------------------------------------------------------------------
# BUILD USER TIMELINE
# ---------------------------------------------------------------------------

def build_financial_timeline(
    dataset: Dataset,
    user_id: str,
) -> FinancialTimeline:
    """
    Build a normalized financial timeline for one user.

    This function:
        1. Gets the user's profile.
        2. Gets their financial events.
        3. Converts known amounts into home currency.
        4. Attaches their messages.
        5. Sorts events chronologically.

    It does NOT:
        - forecast future balances
        - decide affordability
        - invent recurring events
        - modify event status
        - recover image amounts
    """

    if user_id not in dataset.profiles:
        raise KeyError(
            f"Unknown user_id: {user_id}"
        )

    profile = dataset.profiles[user_id]

    user_events = get_user_events(
        dataset,
        user_id,
    )

    rates = ExchangeRateTable(
        dataset.exchange_rates
    )

    normalized_events: list[StateEvent] = []

    for event in user_events:

        normalized_events.append(
            normalize_event(
                event=event,
                profile=profile,
                rates=rates,
            )
        )

    normalized_events.sort(
        key=lambda event: (
            event.effective_date,
            event.event.event_id,
        )
    )

    user_messages = [
        message
        for message in dataset.messages
        if message.user_id == user_id
    ]

    user_messages.sort(
        key=lambda message: message.sent_at
    )

    return FinancialTimeline(
        profile=profile,
        events=normalized_events,
        messages=user_messages,
    )


# ---------------------------------------------------------------------------
# BALANCE DELTA
# ---------------------------------------------------------------------------

def event_balance_delta(
    event: StateEvent,
) -> float:
    """
    Return the balance change caused by an event.

    Positive = money entering the account.
    Negative = money leaving the account.

    Events without a recovered amount contribute nothing for now.
    They must be resolved before being used in authoritative forecasting.
    """

    if event.amount_home_currency is None:
        return 0.0

    if event.is_income:
        return event.amount_home_currency

    if event.is_expense:
        return -event.amount_home_currency

    return 0.0


# ---------------------------------------------------------------------------
# HISTORICAL BALANCE RECONSTRUCTION
# ---------------------------------------------------------------------------

def balance_delta_between(
    timeline: FinancialTimeline,
    start_date: date,
    end_date: date,
) -> float:
    """
    Calculate the net balance movement caused by events in a date range.
    """

    events = timeline.events_between(
        start_date=start_date,
        end_date=end_date,
    )

    return sum(
        event_balance_delta(event)
        for event in events
    )


# ---------------------------------------------------------------------------
# FUTURE EVENT VALIDATION
# ---------------------------------------------------------------------------

def unresolved_amount_events(
    timeline: FinancialTimeline,
) -> list[StateEvent]:
    """
    Return events whose amounts are currently unavailable.

    These cannot safely participate in authoritative financial
    calculations until their amounts are resolved.
    """

    return [
        event
        for event in timeline.events
        if event.amount_home_currency is None
    ]