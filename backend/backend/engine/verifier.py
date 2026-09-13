from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from .models import Payment, PaymentPlan
from .state import FinancialTimeline, ExchangeRateTable
from .forecast import forecast_timeline


@dataclass
class VerificationResult:
    is_safe: bool
    minimum_projected_balance: float
    minimum_required_balance: float
    first_breach_date: date | None = None
    explanation: str = ""


def _forecast(
    timeline: FinancialTimeline,
    start_date: date,
    end_date: date,
    exchange_rates: ExchangeRateTable,
):
    """Build the authoritative recurrence-aware financial forecast."""
    events = [
        state_event.event
        for state_event in timeline.events
    ]

    return forecast_timeline(
        profile=timeline.profile,
        events=events,
        start_date=start_date,
        end_date=end_date,
        exchange_rates=exchange_rates,
    )


def verify_plan(
    timeline: FinancialTimeline,
    plan: PaymentPlan,
    start_date: date,
    end_date: date,
    exchange_rates: ExchangeRateTable,
) -> VerificationResult:
    """
    Verify a payment plan against the user's projected balance.

    The forecast is authoritative for the 90-day financial outlook;
    this function only applies the proposed plan on top of it.
    """
    profile = timeline.profile

    forecast = _forecast(
        timeline=timeline,
        start_date=start_date,
        end_date=end_date,
        exchange_rates=exchange_rates,
    )

    balances = {
        day.date: float(day.ending_balance)
        for day in forecast.days
    }

    if not balances:
        balances[start_date] = float(
            profile.current_available_balance
        )

    # Payments are expressed in the user's home currency.
    for payment in plan.payments:
        payment_date = payment.payment_date
        payment_amount = float(payment.amount)

        if payment_date < start_date or payment_date > end_date:
            return VerificationResult(
                is_safe=False,
                minimum_projected_balance=min(balances.values()),
                minimum_required_balance=profile.minimum_balance_to_keep,
                first_breach_date=payment_date,
                explanation=(
                    "The payment falls outside the safety window."
                ),
            )

        for day in balances:
            if day >= payment_date:
                balances[day] -= payment_amount

    minimum_projected_balance = min(balances.values())
    minimum_required_balance = profile.minimum_balance_to_keep

    first_breach_date = next(
        (
            day
            for day in sorted(balances)
            if balances[day] < minimum_required_balance
        ),
        None,
    )

    is_safe = first_breach_date is None

    if is_safe:
        explanation = (
            "The projected balance stays above the required "
            "minimum throughout the safety window."
        )
    else:
        explanation = (
            "The projected balance falls below the required "
            f"minimum on {first_breach_date.isoformat()}."
        )

    return VerificationResult(
        is_safe=is_safe,
        minimum_projected_balance=minimum_projected_balance,
        minimum_required_balance=minimum_required_balance,
        first_breach_date=first_breach_date,
        explanation=explanation,
    )


def verify_plan_for_request(
    timeline: FinancialTimeline,
    plan: PaymentPlan,
    request_date: date,
    desired_completion_date: date | None = None,
    exchange_rates: ExchangeRateTable | None = None,
) -> VerificationResult:
    """
    Verify a request plan over the full 90-day safety horizon.
    """
    if exchange_rates is None:
        raise ValueError(
            "exchange_rates is required for forecast verification."
        )

    safety_end = request_date + timedelta(days=89)

    if desired_completion_date is not None:
        for payment in plan.payments:
            if payment.payment_date > desired_completion_date:
                return VerificationResult(
                    is_safe=False,
                    minimum_projected_balance=(
                        timeline.profile.current_available_balance
                    ),
                    minimum_required_balance=(
                        timeline.profile.minimum_balance_to_keep
                    ),
                    first_breach_date=payment.payment_date,
                    explanation=(
                        "The payment plan finishes after "
                        "the desired completion date."
                    ),
                )

    return verify_plan(
        timeline=timeline,
        plan=plan,
        start_date=request_date,
        end_date=safety_end,
        exchange_rates=exchange_rates,
    )


def maximum_safe_payment_today(
    timeline: FinancialTimeline,
    request_date: date,
    exchange_rates: ExchangeRateTable,
) -> float:
    """Maximum amount payable today before optional spending changes.

    The challenge defines this as the immediately available balance above
    the user's protected minimum. Future recurring events affect whether
    the *request as a whole* is safe, but not this field.
    """
    profile = timeline.profile
    # The profile balance is the starting balance; confirmed cash flows
    # dated on the request date must be applied before computing what can
    # safely be spent today.
    events = [se.event for se in timeline.events]
    forecast = forecast_timeline(
        profile=profile,
        events=events,
        start_date=request_date,
        end_date=request_date,
        exchange_rates=exchange_rates,
    )
    ending_today = (
        forecast.days[0].ending_balance
        if forecast.days
        else profile.current_available_balance
    )
    return max(
        0.0,
        float(ending_today - profile.minimum_balance_to_keep),
    )

def _single_payment_plan(
    amount: float,
    payment_date: date,
) -> PaymentPlan:
    return PaymentPlan(
        payment_method="full_payment",
        payments=[
            Payment(
                payment_date=payment_date,
                amount=float(amount),
            )
        ],
        total_amount=float(amount),
        financing_fee=0.0,
    )


def find_first_safe_payment_date(
    timeline: FinancialTimeline,
    requested_amount: float,
    start_date: date,
    end_date: date,
    exchange_rates: ExchangeRateTable,
) -> date | None:
    """
    Find the earliest date on which the complete requested amount can
    be paid while satisfying the 90-day safety requirement.

    No optional spending changes are applied here.
    """
    current = start_date

    while current <= end_date:
        plan = _single_payment_plan(
            amount=requested_amount,
            payment_date=current,
        )

        result = verify_plan_for_request(
            timeline=timeline,
            plan=plan,
            request_date=start_date,
            desired_completion_date=current,
            exchange_rates=exchange_rates,
        )

        if result.is_safe:
            return current

        current += timedelta(days=1)

    return None


# Backward-compatible alias for any existing imports.
find_first_safe_full_payment_date = find_first_safe_payment_date


def verify_plan_with_spending_changes(
    timeline: FinancialTimeline,
    plan: PaymentPlan,
    request_date: date,
    desired_completion_date: date | None,
    exchange_rates: ExchangeRateTable,
    changes,
) -> VerificationResult:
    """Verify a plan after applying explicit flexible-spending changes.

    A change is anchored to an observed event and applies to the same
    recurring stream (user, type, description, currency) across future
    occurrences. This keeps the change semantic rather than tied to one
    isolated transaction.
    """
    from dataclasses import replace

    change_map = {c.event_id: c for c in changes}
    targets = {}
    for se in timeline.events:
        if se.event.event_id in change_map:
            ev = se.event
            key = (
                ev.user_id,
                (ev.event_type or "").strip().lower(),
                (ev.description or "").strip().lower(),
                (ev.currency or "").strip().upper(),
            )
            targets[ev.event_id] = key

    changed_events = []
    for se in timeline.events:
        ev = se.event
        amount = ev.amount
        action = None
        for cid, key in targets.items():
            ev0 = next((x.event for x in timeline.events if x.event.event_id == cid), None)
            if ev0 is not None:
                evkey = (
                    ev.user_id,
                    (ev.event_type or "").strip().lower(),
                    (ev.description or "").strip().lower(),
                    (ev.currency or "").strip().upper(),
                )
                if evkey == key and evkey[0] == ev0.user_id:
                    action = change_map[cid]
                    break
        if action is not None:
            if action.action == "stop":
                amount = 0.0
            elif action.action == "reduce_to" and action.new_amount is not None:
                amount = float(action.new_amount)
            ev = replace(ev, amount=amount)
        changed_events.append(ev)

    # Rebuild a lightweight timeline preserving profile/messages.
    changed_timeline = FinancialTimeline(
        profile=timeline.profile,
        events=[
            replace(se, event=ev)
            for se, ev in zip(timeline.events, changed_events)
        ],
        messages=timeline.messages,
    )
    return verify_plan_for_request(
        timeline=changed_timeline,
        plan=plan,
        request_date=request_date,
        desired_completion_date=desired_completion_date,
        exchange_rates=exchange_rates,
    )
