"""
Payment-plan generation for Buy or Wait.

This module generates concrete payment plans that can be evaluated by
the safety verifier.

It does NOT decide whether a plan is safe.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

from .loader import Dataset, get_request_payment_options
from .models import (
    Payment,
    PaymentOption,
    PaymentPlan,
    PurchaseRequest,
)


# ---------------------------------------------------------------------------
# PLAN CANDIDATE
# ---------------------------------------------------------------------------

@dataclass
class PlanCandidate:
    """
    A possible way of completing a financial request.
    """

    payment_method: str

    payments: list[Payment]

    total_amount: float

    financing_fee: float = 0.0

    source_option_id: Optional[str] = None

    is_partial: bool = False

    description: str = ""


# ---------------------------------------------------------------------------
# FULL PAYMENT
# ---------------------------------------------------------------------------

def full_payment_plan(
    request: PurchaseRequest,
) -> PlanCandidate:
    """
    Create a full-payment plan for today/request date.
    """

    payment = Payment(
        payment_date=request.request_date,
        amount=request.requested_amount,
    )

    return PlanCandidate(
        payment_method="full_payment",
        payments=[payment],
        total_amount=request.requested_amount,
        description="Pay the full requested amount on the request date.",
    )


# ---------------------------------------------------------------------------
# PARTIAL PAYMENT
# ---------------------------------------------------------------------------

def partial_payment_plan(
    request: PurchaseRequest,
    safe_amount_today: float,
    earliest_full_payment_date: date,
) -> Optional[PlanCandidate]:
    """
    Create the challenge's two-payment partial-payment structure.

    Payment 1:
        safe amount today

    Payment 2:
        remaining amount on earliest safe full-payment date
    """

    if not request.allows_partial_payment:
        return None

    if safe_amount_today <= 0:
        return None

    if safe_amount_today >= request.requested_amount:
        return None

    if earliest_full_payment_date > (
        request.desired_completion_date
        if request.desired_completion_date
        else earliest_full_payment_date
    ):
        return None

    remaining = (
        request.requested_amount
        - safe_amount_today
    )

    payments = [
        Payment(
            payment_date=request.request_date,
            amount=safe_amount_today,
        ),
        Payment(
            payment_date=earliest_full_payment_date,
            amount=remaining,
        ),
    ]

    return PlanCandidate(
        payment_method="partial_payment",
        payments=payments,
        total_amount=request.requested_amount,
        is_partial=True,
        description=(
            "Pay the safe amount today and the remaining amount "
            "on the earliest safe date."
        ),
    )


# ---------------------------------------------------------------------------
# INSTALLMENT PLAN
# ---------------------------------------------------------------------------

def installment_plan(
    option: PaymentOption,
) -> PlanCandidate:
    """
    Convert one supplied payment option into a concrete plan.

    No installment schedule is invented here. Every payment is derived
    directly from the supplied option.
    """

    if option.number_of_payments <= 0:
        raise ValueError(
            "Payment option must contain at least one payment."
        )

    if option.payment_frequency_days is None:
        if option.number_of_payments != 1:
            raise ValueError(
                "Multiple payments require payment_frequency_days."
            )

    payments: list[Payment] = []

    for index in range(option.number_of_payments):

        if index == 0:
            payment_date = option.first_payment_date
        else:
            payment_date = (
                option.first_payment_date
                + timedelta(
                    days=(
                        option.payment_frequency_days
                        * index
                    )
                )
            )

        payments.append(
            Payment(
                payment_date=payment_date,
                amount=option.payment_amount,
            )
        )

    return PlanCandidate(
        payment_method="installments",
        payments=payments,
        total_amount=option.total_payable_amount,
        financing_fee=option.financing_fee,
        source_option_id=option.payment_option_id,
        description=(
            f"Use supplied installment option "
            f"{option.payment_option_id}."
        ),
    )


# ---------------------------------------------------------------------------
# REQUEST OPTIONS
# ---------------------------------------------------------------------------

def generate_payment_candidates(
    dataset: Dataset,
    request: PurchaseRequest,
) -> list[PlanCandidate]:
    """
    Generate all payment candidates that can be considered for a request.

    Candidates:
        1. Full payment.
        2. Supplied installment options.

    Partial payment is generated separately because it depends on the
    user's calculated safe amount and earliest safe completion date.
    """

    candidates: list[PlanCandidate] = []

    candidates.append(
        full_payment_plan(request)
    )

    supplied_options = get_request_payment_options(
        dataset,
        request.request_id,
    )

    for option in supplied_options:

        if option.payment_method != "installments":
            continue

        candidates.append(
            installment_plan(option)
        )

    return candidates


# ---------------------------------------------------------------------------
# PAYMENT METHOD PREFERENCE
# ---------------------------------------------------------------------------

def payment_method_rank(
    payment_method: str,
    user_preferred_methods: list[str],
) -> int:
    """
    Return a preference score for a payment method.

    Lower is better.

    Methods explicitly preferred by the user rank ahead of methods
    they did not list.
    """

    try:
        return user_preferred_methods.index(
            payment_method
        )
    except ValueError:
        return len(user_preferred_methods)


# ---------------------------------------------------------------------------
# PLAN SUMMARY
# ---------------------------------------------------------------------------

def serialize_payment_plan(
    plan: PlanCandidate,
) -> str:
    """
    Serialize a plan using the challenge output convention:

        YYYY-MM-DD:amount|YYYY-MM-DD:amount
    """

    return "|".join(
        f"{payment.payment_date.isoformat()}:{payment.amount:.2f}" if abs(payment.amount - round(payment.amount)) > 1e-9 else f"{payment.payment_date.isoformat()}:{payment.amount:.0f}"
        for payment in plan.payments
    )


def plan_total_paid(
    plan: PlanCandidate,
) -> float:
    """Sum all actual scheduled payments."""

    return sum(
        payment.amount
        for payment in plan.payments
    )


def plan_last_payment_date(
    plan: PlanCandidate,
) -> Optional[date]:
    """Return the final payment date."""

    if not plan.payments:
        return None

    return max(
        payment.payment_date
        for payment in plan.payments
    )