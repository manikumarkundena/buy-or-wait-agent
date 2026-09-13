"""
Core domain models for Buy or Wait.

These models represent normalized financial data inside the decision engine.
The engine should operate on these models rather than raw CSV rows.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional


# ---------------------------------------------------------------------------
# USER FINANCIAL PROFILE
# ---------------------------------------------------------------------------

@dataclass
class UserProfile:
    user_id: str
    home_currency: str

    current_available_balance: float
    minimum_balance_to_keep: float

    financial_priorities: list[str] = field(default_factory=list)

    expense_categories_to_protect: list[str] = field(default_factory=list)

    expense_categories_user_is_willing_to_reduce: list[str] = field(
        default_factory=list
    )

    expense_categories_user_is_willing_to_stop: list[str] = field(
        default_factory=list
    )

    payment_methods_user_will_consider: list[str] = field(
        default_factory=list
    )

    max_installment_months: Optional[int] = None


# ---------------------------------------------------------------------------
# FINANCIAL EVENT
# ---------------------------------------------------------------------------

@dataclass
class FinancialEvent:
    event_id: str
    user_id: str

    event_type: str
    description: str
    category: str
    direction: str

    amount: Optional[float]
    currency: str

    event_date: date
    settlement_date: Optional[date]

    status: str

    linked_event_id: Optional[str] = None

    flexibility: Optional[str] = None
    minimum_allowed_amount: Optional[float] = None

    # True when the amount was obtained from an image rather than directly
    # from the financial_events.csv amount field.
    amount_from_image: bool = False


# ---------------------------------------------------------------------------
# USER REQUEST
# ---------------------------------------------------------------------------

@dataclass
class PurchaseRequest:
    request_id: str
    user_id: str

    request_date: date
    request_type: str

    requested_amount: float

    desired_completion_date: Optional[date]

    allows_partial_payment: bool

    request_text: str


# ---------------------------------------------------------------------------
# PAYMENT OPTION
# ---------------------------------------------------------------------------

@dataclass
class PaymentOption:
    payment_option_id: str
    request_id: str

    payment_method: str
    payment_amount: float

    number_of_payments: int

    first_payment_date: date

    payment_frequency_days: Optional[int]

    financing_fee: float
    total_payable_amount: float

    @property
    def total_installment_cost(self) -> float:
        """Total amount paid through this option."""
        return self.total_payable_amount


# ---------------------------------------------------------------------------
# MESSAGE
# ---------------------------------------------------------------------------

@dataclass
class FinancialMessage:
    message_id: str
    user_id: str

    request_id: Optional[str]
    related_event_id: Optional[str]

    sent_at: datetime

    source_type: str
    message_text: str


# ---------------------------------------------------------------------------
# EXCHANGE RATE
# ---------------------------------------------------------------------------

@dataclass
class ExchangeRate:
    rate_date: date
    from_currency: str
    to_currency: str

    rate: float


# ---------------------------------------------------------------------------
# PAYMENT PLAN
# ---------------------------------------------------------------------------

@dataclass
class Payment:
    payment_date: date
    amount: float


@dataclass
class PaymentPlan:
    payment_method: str
    payments: list[Payment] = field(default_factory=list)

    total_amount: float = 0.0
    financing_fee: float = 0.0

    def add_payment(self, payment_date: date, amount: float) -> None:
        self.payments.append(
            Payment(
                payment_date=payment_date,
                amount=amount,
            )
        )

        self.total_amount += amount


# ---------------------------------------------------------------------------
# SPENDING CHANGE
# ---------------------------------------------------------------------------

@dataclass
class SpendingChange:
    action: str
    event_id: str
    new_amount: Optional[float] = None

    def serialize(self) -> str:
        """
        Convert to the exact output format required by the challenge.

        Examples:
            stop:event_123
            reduce_to:event_456:500
        """

        if self.action == "stop":
            return f"stop:{self.event_id}"

        if self.action == "reduce_to":
            if self.new_amount is None:
                raise ValueError(
                    "reduce_to spending change requires new_amount"
                )

            return f"reduce_to:{self.event_id}:{self.new_amount:g}"

        raise ValueError(f"Unknown spending change action: {self.action}")


# ---------------------------------------------------------------------------
# DECISION
# ---------------------------------------------------------------------------

@dataclass
class Decision:
    request_id: str

    amount_safe_to_pay: float

    affordability_status: str

    recommended_payment_method: str

    payment_plan: str

    earliest_date_for_full_payment: Optional[date]

    spending_changes_needed: str

    decision_explanation: str

    def to_output_row(self) -> dict:
        """
        Convert the internal decision into the exact output.csv schema.
        """

        return {
            "request_id": self.request_id,
            "amount_safe_to_pay": self.amount_safe_to_pay,
            "affordability_status": self.affordability_status,
            "recommended_payment_method": self.recommended_payment_method,
            "payment_plan": self.payment_plan,
            "earliest_date_for_full_payment": (
                self.earliest_date_for_full_payment.isoformat()
                if self.earliest_date_for_full_payment
                else ""
            ),
            "spending_changes_needed": self.spending_changes_needed,
            "decision_explanation": self.decision_explanation,
        }


# ---------------------------------------------------------------------------
# NORMALIZED USER FINANCIAL STATE
# ---------------------------------------------------------------------------

@dataclass
class FinancialState:
    """
    All information required to reason about a user's financial situation.

    This is deliberately separate from UserProfile because:
      - profile = user's preferences and constraints
      - state   = actual financial events affecting available money
    """

    profile: UserProfile

    events: list[FinancialEvent] = field(default_factory=list)

    messages: list[FinancialMessage] = field(default_factory=list)

    exchange_rates: list[ExchangeRate] = field(default_factory=list)

    def events_for_user(self) -> list[FinancialEvent]:
        """Return events belonging to this user's financial state."""
        return [
            event
            for event in self.events
            if event.user_id == self.profile.user_id
        ]

    def messages_for_user(self) -> list[FinancialMessage]:
        """Return messages belonging to this user."""
        return [
            message
            for message in self.messages
            if message.user_id == self.profile.user_id
        ]