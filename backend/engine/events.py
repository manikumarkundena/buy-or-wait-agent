"""
Financial event interpretation and filtering.

This module decides which financial events are safe to use in
financial-state calculations.

It does NOT make affordability decisions.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Iterable

from .models import FinancialEvent


# ---------------------------------------------------------------------------
# STATUS RULES
# ---------------------------------------------------------------------------

IGNORED_STATUSES = {
    "pending",
    "failed",
    "cancelled",
    "canceled",
}

CONFIRMED_STATUSES = {
    "settled",
    "confirmed",
    "scheduled",
}

UNREALIZED_STATUSES = {
    "unrealized",
}


# ---------------------------------------------------------------------------
# STATUS HELPERS
# ---------------------------------------------------------------------------

def normalized_status(event: FinancialEvent) -> str:
    """Return a normalized event status."""

    return (event.status or "").strip().lower()


def is_ignored(event: FinancialEvent) -> bool:
    """
    Return True when an event must not affect the authoritative
    financial forecast.

    Pending, failed, and cancelled events are not treated as
    committed cash movements.
    """

    return normalized_status(event) in IGNORED_STATUSES


def is_unrealized(event: FinancialEvent) -> bool:
    """
    Return True for non-cash valuation events.

    Investment valuation events are informational and must not
    change available cash.
    """

    return (
        normalized_status(event) in UNREALIZED_STATUSES
        or event.event_type.strip().lower() == "investment_valuation"
    )


def is_confirmed(event: FinancialEvent) -> bool:
    """
    Return True when an event represents a confirmed financial movement.
    """

    return normalized_status(event) in CONFIRMED_STATUSES


# ---------------------------------------------------------------------------
# DIRECTION HELPERS
# ---------------------------------------------------------------------------

def normalized_direction(event: FinancialEvent) -> str:
    """Return normalized transaction direction."""

    return (event.direction or "").strip().lower()


def is_income(event: FinancialEvent) -> bool:
    """Whether the event represents money entering the account."""

    return normalized_direction(event) in {
        "credit",
        "income",
    }


def is_expense(event: FinancialEvent) -> bool:
    """Whether the event represents money leaving the account."""

    return normalized_direction(event) in {
        "debit",
        "expense",
    }


# ---------------------------------------------------------------------------
# EVENT TYPE HELPERS
# ---------------------------------------------------------------------------

def normalized_event_type(event: FinancialEvent) -> str:
    """Return normalized event type."""

    return (event.event_type or "").strip().lower()


def is_investment_valuation(event: FinancialEvent) -> bool:
    """Whether the event is an unrealized investment valuation."""

    return normalized_event_type(event) == "investment_valuation"


def is_investment_purchase(event: FinancialEvent) -> bool:
    """Whether the event represents an investment purchase."""

    return normalized_event_type(event) == "investment_purchase"


def is_investment_sale(event: FinancialEvent) -> bool:
    """Whether the event represents an investment sale."""

    return normalized_event_type(event) == "investment_sale"


def is_refund(event: FinancialEvent) -> bool:
    """Whether the event represents a refund."""

    return normalized_event_type(event) == "refund"


# ---------------------------------------------------------------------------
# AMOUNT VALIDATION
# ---------------------------------------------------------------------------

def has_known_amount(event: FinancialEvent) -> bool:
    """
    Whether the event has a usable direct amount.

    Missing amounts must be resolved separately, for example through
    a related image or message.
    """

    return event.amount is not None


# ---------------------------------------------------------------------------
# FORECAST ELIGIBILITY
# ---------------------------------------------------------------------------

def is_eligible_for_forecast(
    event: FinancialEvent,
) -> bool:
    """
    Determine whether an event can participate in authoritative
    financial forecasting.

    Requirements:
        - event is not ignored
        - event is not an unrealized valuation
        - amount is known
        - event has recognizable financial direction

    Investment valuation events are excluded because they do not
    represent available cash.
    """

    if is_ignored(event):
        return False

    if is_unrealized(event):
        return False

    if not has_known_amount(event):
        return False

    if not (
        is_income(event)
        or is_expense(event)
    ):
        return False

    return True


def eligible_events(
    events: Iterable[FinancialEvent],
) -> list[FinancialEvent]:
    """Return events that are eligible for forecasting."""

    return [
        event
        for event in events
        if is_eligible_for_forecast(event)
    ]


# ---------------------------------------------------------------------------
# DATE HELPERS
# ---------------------------------------------------------------------------

def event_effective_date(
    event: FinancialEvent,
) -> date:
    """
    Return the date on which an event should affect the financial timeline.

    Settlement date takes precedence when available.
    """

    return event.settlement_date or event.event_date


def future_events(
    events: Iterable[FinancialEvent],
    start_date: date,
    end_date: date,
) -> list[FinancialEvent]:
    """
    Return eligible events occurring inside the requested future window.
    """

    result = []

    for event in eligible_events(events):
        effective_date = event_effective_date(event)

        if start_date <= effective_date <= end_date:
            result.append(event)

    return sorted(
        result,
        key=lambda event: (
            event_effective_date(event),
            event.event_id,
        ),
    )


# ---------------------------------------------------------------------------
# FLEXIBILITY
# ---------------------------------------------------------------------------

FLEXIBILITY_FIXED = "fixed"
FLEXIBILITY_REDUCIBLE = "reducible"
FLEXIBILITY_STOPPABLE = "stoppable"
FLEXIBILITY_REDUCIBLE_OR_STOPPABLE = "reducible_or_stoppable"


def normalized_flexibility(event: FinancialEvent) -> str:
    """Return normalized flexibility metadata."""

    return (event.flexibility or "").strip().lower()


def is_flexible(event: FinancialEvent) -> bool:
    """
    Whether an expense is flexible.

    The dataset explicitly represents flexible spending using:
        - reducible
        - stoppable
        - reducible_or_stoppable

    Fixed expenses are never treated as flexible.
    """

    return (
        is_expense(event)
        and normalized_flexibility(event) in {
            FLEXIBILITY_REDUCIBLE,
            FLEXIBILITY_STOPPABLE,
            FLEXIBILITY_REDUCIBLE_OR_STOPPABLE,
        }
    )


def is_fixed(event: FinancialEvent) -> bool:
    """Whether an expense is explicitly fixed."""

    return (
        is_expense(event)
        and normalized_flexibility(event)
        == FLEXIBILITY_FIXED
    )


def is_reducible(event: FinancialEvent) -> bool:
    """
    Whether an expense is marked as reducible.

    A reducible_or_stoppable event is also reducible.
    """

    return (
        is_expense(event)
        and normalized_flexibility(event) in {
            FLEXIBILITY_REDUCIBLE,
            FLEXIBILITY_REDUCIBLE_OR_STOPPABLE,
        }
    )


def is_stoppable(event: FinancialEvent) -> bool:
    """
    Whether an expense is marked as stoppable.

    A reducible_or_stoppable event is also stoppable.
    """

    return (
        is_expense(event)
        and normalized_flexibility(event) in {
            FLEXIBILITY_STOPPABLE,
            FLEXIBILITY_REDUCIBLE_OR_STOPPABLE,
        }
    )


# ---------------------------------------------------------------------------
# SPENDING-CHANGE ELIGIBILITY
# ---------------------------------------------------------------------------

def can_reduce(event: FinancialEvent) -> bool:
    """
    Whether this event can potentially be reduced.

    A reduction requires:
        - expense
        - reducible flexibility
        - known current amount
        - defined minimum allowed amount
        - minimum is lower than current amount
    """

    return (
        is_reducible(event)
        and event.minimum_allowed_amount is not None
        and event.amount is not None
        and event.minimum_allowed_amount < event.amount
    )


def can_stop(
    event: FinancialEvent,
    profile_categories: list[str],
) -> bool:
    """
    Whether an expense can potentially be stopped.

    The user's explicit willingness to stop the category is required.

    `profile_categories` should contain the categories the user has
    explicitly marked as willing to stop.
    """

    if not is_stoppable(event):
        return False

    if not profile_categories:
        return False

    allowed_categories = {
        str(category).strip().lower()
        for category in profile_categories
    }

    event_category = (
        event.category or ""
    ).strip().lower()

    return event_category in allowed_categories


# ---------------------------------------------------------------------------
# EVENT GROUPING
# ---------------------------------------------------------------------------

def group_by_category(
    events: Iterable[FinancialEvent],
) -> dict[str, list[FinancialEvent]]:
    """Group events by financial category."""

    grouped: dict[str, list[FinancialEvent]] = {}

    for event in events:
        grouped.setdefault(
            event.category,
            [],
        ).append(event)

    return grouped


def group_by_user(
    events: Iterable[FinancialEvent],
) -> dict[str, list[FinancialEvent]]:
    """Group events by user ID."""

    grouped: dict[str, list[FinancialEvent]] = {}

    for event in events:
        grouped.setdefault(
            event.user_id,
            [],
        ).append(event)

    return grouped


# ---------------------------------------------------------------------------
# RECURRENCE DETECTION
# ---------------------------------------------------------------------------

def _same_description_group(a: FinancialEvent, b: FinancialEvent) -> bool:
    return (
        a.user_id == b.user_id
        and a.event_type == b.event_type
        and a.description == b.description
        and a.currency == b.currency
    )

def infer_recurrence_interval(
    events: Iterable[FinancialEvent],
) -> int | None:
    """
    Infer a recurring interval from observed historical dates.

    We only infer recurrence when the same interval appears at least
    twice. This avoids guessing recurrence from two unrelated events.

    Examples:
        7  -> approximately weekly
        14 -> approximately biweekly
        28/29/30/31 -> approximately monthly
    """

    dates = sorted({
        event_effective_date(event)
        for event in events
        if event_effective_date(event) is not None
    })

    if len(dates) < 3:
        return None

    gaps = [
        (dates[index] - dates[index - 1]).days
        for index in range(1, len(dates))
    ]

    if not gaps:
        return None

    counts: dict[int, int] = defaultdict(int)

    for gap in gaps:
        if gap > 0:
            counts[gap] += 1

    if not counts:
        return None

        most_common_gap, frequency = max(
        counts.items(),
        key=lambda item: item[1],
    )

    if frequency < 2:
        return None

    # Require the dominant interval to explain most of the observed gaps.
    # This prevents unrelated transactions in the same description group
    # from being mistaken for a recurring stream.
    required_frequency = max(2, (len(gaps) * 3 + 3) // 4)

    if frequency < required_frequency:
        return None

    return most_common_gap


def recurring_event_groups(
    events: Iterable[FinancialEvent],
) -> list[dict]:
    """
    Detect recurring event groups from historical events.

    Only eligible cash-flow events are considered.

    Each result contains:
        key
        events
        interval_days
    """

    eligible = eligible_events(events)
    groups = _same_description_group(eligible)

    recurring = []

    for key, group in groups.items():
        if len(group) < 3:
            continue

        interval = infer_recurrence_interval(group)

        if interval is None:
            continue

        recurring.append({
            "key": key,
            "events": sorted(
                group,
                key=event_effective_date,
            ),
            "interval_days": interval,
        })

    return recurring


def project_recurring_events(
    events: Iterable[FinancialEvent],
    start_date: date,
    end_date: date,
) -> list[tuple[FinancialEvent, date]]:
    """Generate future occurrences from historical recurring streams.

    Monthly streams are projected by calendar month (preserving the observed
    day-of-month) rather than by a fixed 30-day interval, which avoids
    cumulative date drift. Weekly/biweekly and other exact intervals retain
    their observed day gap.
    """
    projected: list[tuple[FinancialEvent, date]] = []

    def add_month(d: date) -> date:
        # Avoid a new dependency: clamp to the last valid day of the target
        # month while preserving the original day where possible.
        import calendar
        year = d.year + (d.month // 12)
        month = (d.month % 12) + 1
        day = min(d.day, calendar.monthrange(year, month)[1])
        return date(year, month, day)

    for group in recurring_event_groups(events):
        group_events = group["events"]
        interval = group["interval_days"]

        historical = [
            event for event in group_events
            if event_effective_date(event) < start_date
        ]
        if not historical:
            continue

        latest = historical[-1]
        latest_date = event_effective_date(latest)

        # 28-31 day recurrence is a calendar-month stream.
        monthly = 28 <= interval <= 31
        next_date = add_month(latest_date) if monthly else (
            latest_date + timedelta(days=interval)
        )

        while next_date <= end_date:
            if next_date >= start_date:
                projected.append((latest, next_date))

            next_date = (
                add_month(next_date)
                if monthly
                else next_date + timedelta(days=interval)
            )

    return sorted(
        projected,
        key=lambda item: (item[1], item[0].event_id),
    )
