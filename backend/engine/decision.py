"""
Final decision engine for Buy or Wait.

This module combines:
    - user financial state
    - payment-plan candidates
    - safety verification

and produces the challenge's required decision fields.

The verifier remains authoritative for financial safety.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

from .events import is_flexible
from .loader import Dataset, get_request_payment_options
from .models import (
    Decision,
    FinancialEvent,
    Payment,
    PurchaseRequest,
    SpendingChange,
)
from .planner import (
    PlanCandidate,
    generate_payment_candidates,
    partial_payment_plan,
    plan_last_payment_date,
    serialize_payment_plan,
)
from .state import FinancialTimeline, ExchangeRateTable
from .verifier import (
    VerificationResult,
    find_first_safe_payment_date,
    maximum_safe_payment_today,
    verify_plan_for_request,
    verify_plan_with_spending_changes,
)


# ---------------------------------------------------------------------------
# ALLOWED OUTPUT VALUES
# ---------------------------------------------------------------------------

AFFORDABILITY_STATUSES = {
    "affordable_now",
    "affordable_with_plan",
    "affordable_later",
    "not_affordable",
}

PAYMENT_METHODS = {
    "full_payment",
    "partial_payment",
    "installments",
    "wait",
    "not_recommended",
}


# ---------------------------------------------------------------------------
# VERIFIED PLAN
# ---------------------------------------------------------------------------

@dataclass
class VerifiedPlan:
    plan: PlanCandidate
    verification: VerificationResult


# ---------------------------------------------------------------------------
# SPENDING CHANGES
# ---------------------------------------------------------------------------

def _eligible_spending_changes(
    timeline: FinancialTimeline,
) -> list[FinancialEvent]:
    """
    Return flexible expenses that the user has explicitly indicated
    they are willing to reduce or stop.
    """

    profile = timeline.profile

    eligible: list[FinancialEvent] = []

    for state_event in timeline.events:

        event = state_event.event

        if not is_flexible(event):
            continue

        can_reduce = (
            event.category
            in profile.expense_categories_user_is_willing_to_reduce
            and event.minimum_allowed_amount is not None
            and event.amount is not None
            and event.minimum_allowed_amount < event.amount
        )

        can_stop = (
            event.category
            in profile.expense_categories_user_is_willing_to_stop
        )

        if can_reduce or can_stop:
            eligible.append(event)

    return eligible


def spending_changes_for_plan(
    timeline: FinancialTimeline,
    additional_amount_needed: float,
) -> list[SpendingChange]:
    """
    Find up to three flexible spending changes that could offset
    an affordability gap.

    Changes are ordered conservatively:
        1. reductions
        2. stops

    No more than three changes are returned.
    """

    if additional_amount_needed <= 0:
        return []

    profile = timeline.profile

    candidates = _eligible_spending_changes(timeline)

    reductions: list[tuple[float, SpendingChange]] = []
    stops: list[tuple[float, SpendingChange]] = []

    for event in candidates:

        amount = event.amount or 0.0

        if (
            event.category
            in profile.expense_categories_user_is_willing_to_reduce
            and event.minimum_allowed_amount is not None
            and event.minimum_allowed_amount < amount
        ):
            savings = (
                amount
                - event.minimum_allowed_amount
            )

            reductions.append(
                (
                    savings,
                    SpendingChange(
                        action="reduce_to",
                        event_id=event.event_id,
                        new_amount=event.minimum_allowed_amount,
                    ),
                )
            )

        if (
            event.category
            in profile.expense_categories_user_is_willing_to_stop
        ):
            stops.append(
                (
                    amount,
                    SpendingChange(
                        action="stop",
                        event_id=event.event_id,
                    ),
                )
            )

    # Prefer the smallest set of changes that can cover the gap.
    reductions.sort(
        key=lambda item: item[0],
        reverse=True,
    )

    stops.sort(
        key=lambda item: item[0],
        reverse=True,
    )

    selected: list[SpendingChange] = []
    savings = 0.0

    for saved_amount, change in (
        reductions + stops
    ):

        selected.append(change)
        savings += saved_amount

        if savings >= additional_amount_needed:
            break

        if len(selected) >= 3:
            break

    if savings < additional_amount_needed:
        return []

    return selected[:3]


def serialize_spending_changes(
    changes: list[SpendingChange],
) -> str:
    """Serialize spending changes for output.csv."""

    if not changes:
        return "none"

    return "|".join(
        change.serialize()
        for change in changes
    )


# ---------------------------------------------------------------------------
# PLAN RANKING
# ---------------------------------------------------------------------------

def _plan_rank(
    verified: VerifiedPlan,
    timeline: FinancialTimeline,
) -> tuple:
    """
    Rank verified plans.

    Preference order:
        1. full payment
        2. installments
        3. partial payment

    Within equivalent plans:
        - preserve more money
        - prefer lower total cost
        - respect user's payment-method preferences
    """

    plan = verified.plan

    method_priority = {
        "full_payment": 0,
        "installments": 1,
        "partial_payment": 2,
    }

    preference_rank = (
        timeline.profile.payment_methods_user_will_consider
    )

    user_method_rank = (
        preference_rank.index(plan.payment_method)
        if plan.payment_method in preference_rank
        else len(preference_rank)
    )

    return (
        method_priority.get(
            plan.payment_method,
            99,
        ),
        user_method_rank,
        plan.total_amount,
        plan.financing_fee,
        plan_last_payment_date(plan)
        or date.max,
    )


# ---------------------------------------------------------------------------
# SAFE PLAN SELECTION
# ---------------------------------------------------------------------------

def select_best_safe_plan(
    verified_plans: list[VerifiedPlan],
    timeline: FinancialTimeline,
) -> Optional[VerifiedPlan]:

    safe_plans = [
        plan
        for plan in verified_plans
        if plan.verification.is_safe
    ]

    if not safe_plans:
        return None

    safe_plans.sort(
        key=lambda plan: _plan_rank(
            plan,
            timeline,
        )
    )

    return safe_plans[0]


# ---------------------------------------------------------------------------
# FULL PAYMENT DATE
# ---------------------------------------------------------------------------

def _full_payment_date(
    request: PurchaseRequest,
) -> date:
    return request.request_date


# ---------------------------------------------------------------------------
# DECISION EXPLANATION
# ---------------------------------------------------------------------------

def _money(
    amount: float,
    currency: str,
) -> str:
    """
    Basic explanation formatting.

    The frontend will have a richer locale-aware formatter later.
    """

    return (
        f"{currency} {amount:,.0f}"
        if abs(amount - round(amount)) < 1e-9
        else f"{currency} {amount:,.2f}"
    )


def _build_now_explanation(
    request: PurchaseRequest,
    timeline: FinancialTimeline,
    verification: VerificationResult,
) -> str:

    currency = timeline.profile.home_currency

    return (
        f"Pay {_money(request.requested_amount, currency)} today. "
        f"This leaves at least "
        f"{_money(timeline.profile.minimum_balance_to_keep, currency)} "
        f"available over the next 90 days."
    )


def _build_plan_explanation(
    request: PurchaseRequest,
    timeline: FinancialTimeline,
    plan: VerifiedPlan,
) -> str:

    currency = timeline.profile.home_currency

    payments = plan.plan.payments
    if plan.plan.payment_method == "installments" and payments:
        first = payments[0]
        count = len(payments)
        return (
            f"Use {count} installments of "
            f"{_money(first.amount, currency)}, starting "
            f"{first.payment_date.strftime('%-d %B %Y') if False else first.payment_date.strftime('%d %B %Y').lstrip('0')}. "
            f"This leaves at least "
            f"{_money(timeline.profile.minimum_balance_to_keep, currency)} available."
        )
    return (
        f"Use the supplied {plan.plan.payment_method} plan. "
        f"The projected balance remains above the "
        f"{_money(timeline.profile.minimum_balance_to_keep, currency)} "
        f"minimum throughout the safety window."
    )


def _build_later_explanation(
    request: PurchaseRequest,
    timeline: FinancialTimeline,
    safe_date: date,
) -> str:

    currency = timeline.profile.home_currency

    return (
        f"Pay {_money(request.requested_amount, currency)} in full on "
        f"{safe_date.strftime('%d %B %Y').lstrip('0')}. "
        f"Paying earlier would take the balance below the "
        f"{_money(timeline.profile.minimum_balance_to_keep, currency)} minimum."
    )


def _build_not_affordable_explanation(
    request: PurchaseRequest,
    timeline: FinancialTimeline,
) -> str:

    currency = timeline.profile.home_currency

    return (
        f"The requested {_money(request.requested_amount, currency)} "
        f"cannot be safely completed within the allowed timeframe "
        f"while preserving the "
        f"{_money(timeline.profile.minimum_balance_to_keep, currency)} "
        f"minimum balance."
    )




def _changes_text(
    changes: list[SpendingChange],
    timeline: FinancialTimeline,
) -> str:
    labels = []
    by_id = {se.event.event_id: se.event for se in timeline.events}
    for change in changes:
        event = by_id.get(change.event_id)
        label = (event.description if event else change.event_id) or change.event_id
        if change.action == "stop":
            labels.append(f"Stop the {label}")
        else:
            labels.append(
                f"Reduce the {label} to "
                f"{_money(change.new_amount or 0.0, timeline.profile.home_currency)}"
            )
    if len(labels) == 1:
        return labels[0]
    if len(labels) == 2:
        return f"{labels[0]} and {labels[1]}"
    return ", ".join(labels[:-1]) + f", and {labels[-1]}"

# ---------------------------------------------------------------------------
# FINAL DECISION
# ---------------------------------------------------------------------------

def decide(
    dataset: Dataset,
    request: PurchaseRequest,
) -> Decision:
    """
    Produce the final decision for one request.
    """

    if request.user_id not in dataset.profiles:
        raise KeyError(
            f"Unknown user: {request.user_id}"
        )

    # Import here to keep the module dependency graph simple.
    from .state import build_financial_timeline

    timeline = build_financial_timeline(
        dataset,
        request.user_id,
    )
    rates = ExchangeRateTable(
        dataset.exchange_rates
    )

    # ---------------------------------------------------------------
    # 1. Generate normal payment candidates.
    # ---------------------------------------------------------------

    candidates = generate_payment_candidates(
        dataset,
        request,
    )

    # ---------------------------------------------------------------
    # 2. Verify every candidate.
    # ---------------------------------------------------------------

    verified_plans: list[VerifiedPlan] = []

    for candidate in candidates:

        verification = verify_plan_for_request(
            timeline=timeline,
            plan=candidate,
            request_date=request.request_date,
            desired_completion_date=(
                request.desired_completion_date
            ),
            exchange_rates=rates,
        )

        verified_plans.append(
            VerifiedPlan(
                plan=candidate,
                verification=verification,
            )
        )

    # ---------------------------------------------------------------
    # 3. Is full payment safe today?
    # ---------------------------------------------------------------

    full_payment_verified = next(
        (
            plan
            for plan in verified_plans
            if plan.plan.payment_method == "full_payment"
        ),
        None,
    )

    if (
        full_payment_verified is not None
        and full_payment_verified.verification.is_safe
    ):

        return Decision(
            request_id=request.request_id,
            amount_safe_to_pay=request.requested_amount,
            affordability_status="affordable_now",
            recommended_payment_method="full_payment",
            payment_plan=serialize_payment_plan(
                full_payment_verified.plan
            ),
            earliest_date_for_full_payment=(
                request.request_date
            ),
            spending_changes_needed="none",
            decision_explanation=_build_now_explanation(
                request,
                timeline,
                full_payment_verified.verification,
            ),
        )

    # ---------------------------------------------------------------
    # 4. Try the full payment with optional spending changes.
    # ---------------------------------------------------------------
    # First establish the baseline breach, then select the smallest set
    # of user-authorized flexible recurring changes that can remove it.
    baseline_full = full_payment_verified.verification if full_payment_verified else None
    if full_payment_verified is not None and baseline_full is not None and not baseline_full.is_safe:
        needed = max(
            0.0,
            timeline.profile.minimum_balance_to_keep
            - baseline_full.minimum_projected_balance,
        )
        changes = spending_changes_for_plan(timeline, needed)
        if changes:
            changed_verification = verify_plan_with_spending_changes(
                timeline=timeline,
                plan=full_payment_verified.plan,
                request_date=request.request_date,
                desired_completion_date=request.desired_completion_date,
                exchange_rates=rates,
                changes=changes,
            )
            if changed_verification.is_safe:
                return Decision(
                    request_id=request.request_id,
                    amount_safe_to_pay=safe_today if 'safe_today' in locals() else min(
                        request.requested_amount,
                        maximum_safe_payment_today(timeline, request.request_date, rates),
                    ),
                    affordability_status="affordable_with_plan",
                    recommended_payment_method="full_payment",
                    payment_plan=serialize_payment_plan(full_payment_verified.plan),
                    earliest_date_for_full_payment=None,
                    spending_changes_needed=serialize_spending_changes(changes),
                    decision_explanation=(
                        f"{_changes_text(changes, timeline)}, then pay "
                        f"{_money(request.requested_amount, timeline.profile.home_currency)} "
                        f"today. This leaves at least "
                        f"{_money(timeline.profile.minimum_balance_to_keep, timeline.profile.home_currency)} available."
                    ),
                )

    # ---------------------------------------------------------------
    # 5. Determine the maximum safe amount today.
    # ---------------------------------------------------------------

    safe_today = min(
        request.requested_amount,
        maximum_safe_payment_today(
            timeline,
            request.request_date,
            rates,
        ),
    )

    # ---------------------------------------------------------------
    # 5. Find the earliest safe date for full payment.
    # ---------------------------------------------------------------

    search_end = (
        request.desired_completion_date
        or (
            request.request_date
            + timedelta(days=89)
        )
    )

    earliest_safe_date = find_first_safe_payment_date(
        timeline=timeline,
        requested_amount=request.requested_amount,
        start_date=request.request_date,
        end_date=search_end,
        exchange_rates=rates,
    )

    # ---------------------------------------------------------------
    # 6. Prefer a safe supplied installment option.
    # ---------------------------------------------------------------
    installment_plans = [
        plan for plan in verified_plans
        if plan.plan.payment_method == "installments"
        and plan.verification.is_safe
    ]

    if installment_plans:
        best_safe_plan = select_best_safe_plan(
            installment_plans,
            timeline,
        )
        return Decision(
            request_id=request.request_id,
            amount_safe_to_pay=safe_today,
            affordability_status="affordable_with_plan",
            recommended_payment_method="installments",
            payment_plan=serialize_payment_plan(best_safe_plan.plan),
            earliest_date_for_full_payment=earliest_safe_date,
            spending_changes_needed="none",
            decision_explanation=_build_plan_explanation(
                request, timeline, best_safe_plan
            ),
        )

    # ---------------------------------------------------------------
    # 7. Check partial payment.
    # ---------------------------------------------------------------
    if (
        request.allows_partial_payment
        and safe_today > 0
        and safe_today < request.requested_amount
        and earliest_safe_date is not None
    ):
        partial = partial_payment_plan(
            request=request,
            safe_amount_today=safe_today,
            earliest_full_payment_date=earliest_safe_date,
        )

        if partial is not None:
            verification = verify_plan_for_request(
                timeline=timeline,
                plan=partial,
                request_date=request.request_date,
                desired_completion_date=request.desired_completion_date,
                exchange_rates=rates,
            )
            if verification.is_safe:
                return Decision(
                    request_id=request.request_id,
                    amount_safe_to_pay=safe_today,
                    affordability_status="affordable_with_plan",
                    recommended_payment_method="partial_payment",
                    payment_plan=serialize_payment_plan(partial),
                    earliest_date_for_full_payment=earliest_safe_date,
                    spending_changes_needed="none",
                    decision_explanation=(
                        f"Pay {_money(safe_today, timeline.profile.home_currency)} "
                        f"today and the remaining amount on "
                        f"{earliest_safe_date.isoformat()}."
                    ),
                )

    # ---------------------------------------------------------------
    # 8. Check whether the full amount can be paid later.
    # ---------------------------------------------------------------

    if earliest_safe_date is not None:

        return Decision(
            request_id=request.request_id,
            amount_safe_to_pay=safe_today,
            affordability_status="affordable_later",
            recommended_payment_method="wait",
            payment_plan=(
                f"{earliest_safe_date.isoformat()}:"
                f"{request.requested_amount:.0f}"
                if abs(request.requested_amount - round(request.requested_amount)) < 1e-9
                else f"{earliest_safe_date.isoformat()}:{request.requested_amount:.2f}"
            ),
            earliest_date_for_full_payment=(
                earliest_safe_date
            ),
            spending_changes_needed="none",
            decision_explanation=_build_later_explanation(
                request,
                timeline,
                earliest_safe_date,
            ),
        )

    # ---------------------------------------------------------------
    # 9. Nothing works.
    # ---------------------------------------------------------------

    return Decision(
        request_id=request.request_id,
        amount_safe_to_pay=safe_today,
        affordability_status="not_affordable",
        recommended_payment_method="not_recommended",
        payment_plan="none",
        earliest_date_for_full_payment=None,
        spending_changes_needed="none",
        decision_explanation=_build_not_affordable_explanation(
            request,
            timeline,
        ),
    )