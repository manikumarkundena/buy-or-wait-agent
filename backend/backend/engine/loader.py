"""
Dataset loader for Buy or Wait.

Responsible for:
- Reading the challenge CSV files.
- Converting raw CSV values into domain models.
- Normalizing dates, numbers, booleans, and pipe-separated fields.
- Preserving missing financial-event amounts as None.
- Never modifying the source dataset.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from .models import (
    ExchangeRate,
    FinancialEvent,
    FinancialMessage,
    PaymentOption,
    PurchaseRequest,
    UserProfile,
)


# ---------------------------------------------------------------------------
# DATASET CONTAINER
# ---------------------------------------------------------------------------

@dataclass
class Dataset:
    """
    Fully loaded challenge dataset.

    All raw CSV rows are converted into domain objects before entering
    the financial engine.
    """

    profiles: dict[str, UserProfile]
    events: list[FinancialEvent]
    requests: list[PurchaseRequest]
    payment_options: list[PaymentOption]
    messages: list[FinancialMessage]
    exchange_rates: list[ExchangeRate]

    # Image metadata is kept as a DataFrame for now because the actual
    # image amount extraction belongs to the multimodal interpretation layer.
    images: pd.DataFrame


# ---------------------------------------------------------------------------
# GENERIC HELPERS
# ---------------------------------------------------------------------------

def _clean_string(value) -> Optional[str]:
    """Convert pandas missing values to None and strip whitespace."""

    if pd.isna(value):
        return None

    value = str(value).strip()

    if not value:
        return None

    return value


def _to_float(value) -> Optional[float]:
    """Safely convert a value to float."""

    if pd.isna(value):
        return None

    return float(value)


def _to_int(value) -> Optional[int]:
    """Safely convert a value to int."""

    if pd.isna(value):
        return None

    return int(float(value))


def _to_date(value) -> Optional[date]:
    """Convert a CSV date into datetime.date."""

    if pd.isna(value):
        return None

    return pd.to_datetime(value).date()


def _to_datetime(value) -> Optional[datetime]:
    """Convert a timestamp into a Python datetime."""

    if pd.isna(value):
        return None

    return pd.to_datetime(value, utc=True).to_pydatetime()


def _split_pipe(value) -> list[str]:
    """
    Convert challenge fields such as:

        education|debt_repayment

    into:

        ["education", "debt_repayment"]
    """

    value = _clean_string(value)

    if value is None:
        return []

    return [
        item.strip()
        for item in value.split("|")
        if item.strip()
    ]


def _to_bool(value) -> bool:
    """Convert common CSV boolean representations into bool."""

    if isinstance(value, bool):
        return value

    if pd.isna(value):
        return False

    value = str(value).strip().lower()

    if value in {"true", "1", "yes"}:
        return True

    if value in {"false", "0", "no"}:
        return False

    raise ValueError(f"Cannot convert value to bool: {value!r}")


# ---------------------------------------------------------------------------
# PROFILES
# ---------------------------------------------------------------------------

def load_profiles(path: Path) -> dict[str, UserProfile]:
    """Load financial_profiles.csv."""

    df = pd.read_csv(path)

    profiles: dict[str, UserProfile] = {}

    for row in df.to_dict(orient="records"):
        profile = UserProfile(
            user_id=str(row["user_id"]),
            home_currency=str(row["home_currency"]).strip(),

            current_available_balance=float(
                row["current_available_balance"]
            ),

            minimum_balance_to_keep=float(
                row["minimum_balance_to_keep"]
            ),

            financial_priorities=_split_pipe(
                row["financial_priorities"]
            ),

            expense_categories_to_protect=_split_pipe(
                row["expense_categories_to_protect"]
            ),

            expense_categories_user_is_willing_to_reduce=_split_pipe(
                row[
                    "expense_categories_user_is_willing_to_reduce"
                ]
            ),

            expense_categories_user_is_willing_to_stop=_split_pipe(
                row[
                    "expense_categories_user_is_willing_to_stop"
                ]
            ),

            payment_methods_user_will_consider=_split_pipe(
                row[
                    "payment_methods_user_will_consider"
                ]
            ),

            max_installment_months=_to_int(
                row["max_installment_months"]
            ),
        )

        profiles[profile.user_id] = profile

    return profiles


# ---------------------------------------------------------------------------
# FINANCIAL EVENTS
# ---------------------------------------------------------------------------

def load_events(path: Path) -> list[FinancialEvent]:
    """
    Load financial_events.csv.

    IMPORTANT:
    A missing amount is kept as None.

    We do NOT convert a missing amount into 0 because some challenge
    events require the amount to be recovered from a related image.
    """

    df = pd.read_csv(path)

    events: list[FinancialEvent] = []

    for row in df.to_dict(orient="records"):

        amount = _to_float(row["amount"])

        event = FinancialEvent(
            event_id=str(row["event_id"]),
            user_id=str(row["user_id"]),

            event_type=str(row["event_type"]).strip(),
            description=str(row["description"]).strip(),
            category=str(row["category"]).strip(),
            direction=str(row["direction"]).strip(),

            amount=amount,

            currency=str(row["currency"]).strip(),

            event_date=_to_date(row["event_date"]),
            settlement_date=_to_date(row["settlement_date"]),

            status=str(row["status"]).strip(),

            linked_event_id=_clean_string(
                row["linked_event_id"]
            ),

            flexibility=_clean_string(
                row["flexibility"]
            ),

            minimum_allowed_amount=_to_float(
                row["minimum_allowed_amount"]
            ),

            amount_from_image=(amount is None),
        )

        events.append(event)

    return events


# ---------------------------------------------------------------------------
# REQUESTS
# ---------------------------------------------------------------------------

def load_requests(path: Path) -> list[PurchaseRequest]:
    """Load requests.csv."""

    df = pd.read_csv(path)

    requests: list[PurchaseRequest] = []

    for row in df.to_dict(orient="records"):

        request = PurchaseRequest(
            request_id=str(row["request_id"]),
            user_id=str(row["user_id"]),

            request_date=_to_date(
                row["request_date"]
            ),

            request_type=str(
                row["request_type"]
            ).strip(),

            requested_amount=float(
                row["requested_amount"]
            ),

            desired_completion_date=_to_date(
                row["desired_completion_date"]
            ),

            allows_partial_payment=_to_bool(
                row["allows_partial_payment"]
            ),

            request_text=str(
                row["request_text"]
            ).strip(),
        )

        requests.append(request)

    return requests


# ---------------------------------------------------------------------------
# PAYMENT OPTIONS
# ---------------------------------------------------------------------------

def load_payment_options(
    path: Path,
) -> list[PaymentOption]:
    """Load request_payment_options.csv."""

    df = pd.read_csv(path)

    options: list[PaymentOption] = []

    for row in df.to_dict(orient="records"):

        option = PaymentOption(
            payment_option_id=str(
                row["payment_option_id"]
            ),

            request_id=str(
                row["request_id"]
            ),

            payment_method=str(
                row["payment_method"]
            ).strip(),

            payment_amount=float(
                row["payment_amount"]
            ),

            number_of_payments=int(
                row["number_of_payments"]
            ),

            first_payment_date=_to_date(
                row["first_payment_date"]
            ),

            payment_frequency_days=_to_int(
                row["payment_frequency_days"]
            ),

            financing_fee=float(
                row["financing_fee"]
            ),

            total_payable_amount=float(
                row["total_payable_amount"]
            ),
        )

        options.append(option)

    return options


# ---------------------------------------------------------------------------
# MESSAGES
# ---------------------------------------------------------------------------

def load_messages(path: Path) -> list[FinancialMessage]:
    """Load messages.csv."""

    df = pd.read_csv(path)

    messages: list[FinancialMessage] = []

    for row in df.to_dict(orient="records"):

        message = FinancialMessage(
            message_id=str(
                row["message_id"]
            ),

            user_id=str(
                row["user_id"]
            ),

            request_id=_clean_string(
                row["request_id"]
            ),

            related_event_id=_clean_string(
                row["related_event_id"]
            ),

            sent_at=_to_datetime(
                row["sent_at"]
            ),

            source_type=str(
                row["source_type"]
            ).strip(),

            message_text=str(
                row["message_text"]
            ).strip(),
        )

        messages.append(message)

    return messages


# ---------------------------------------------------------------------------
# EXCHANGE RATES
# ---------------------------------------------------------------------------

def load_exchange_rates(
    path: Path,
) -> list[ExchangeRate]:
    """Load exchange_rates.csv."""

    df = pd.read_csv(path)

    rates: list[ExchangeRate] = []

    for row in df.to_dict(orient="records"):

        rate = ExchangeRate(
            rate_date=_to_date(
                row["rate_date"]
            ),

            from_currency=str(
                row["from_currency"]
            ).strip(),

            to_currency=str(
                row["to_currency"]
            ).strip(),

            rate=float(
                row["rate"]
            ),
        )

        rates.append(rate)

    return rates


# ---------------------------------------------------------------------------
# IMAGE METADATA
# ---------------------------------------------------------------------------

def load_images(path: Path) -> pd.DataFrame:
    """
    Load images.csv.

    The image metadata is preserved as-is.

    Actual image interpretation will happen in a separate layer.
    """

    return pd.read_csv(path)


# ---------------------------------------------------------------------------
# COMPLETE DATASET
# ---------------------------------------------------------------------------

def load_dataset(
    dataset_dir: str | Path,
) -> Dataset:

    """
    Load the complete Buy or Wait dataset.

    Parameters
    ----------
    dataset_dir:
        Path to the challenge dataset directory.
    """

    dataset_dir = Path(dataset_dir)

    if not dataset_dir.exists():
        raise FileNotFoundError(
            f"Dataset directory does not exist: {dataset_dir}"
        )

    required_files = [
        "financial_profiles.csv",
        "financial_events.csv",
        "requests.csv",
        "request_payment_options.csv",
        "messages.csv",
        "exchange_rates.csv",
        "images.csv",
    ]

    missing_files = [
        filename
        for filename in required_files
        if not (dataset_dir / filename).exists()
    ]

    if missing_files:
        raise FileNotFoundError(
            "Missing dataset files:\n"
            + "\n".join(
                f"  - {filename}"
                for filename in missing_files
            )
        )

    profiles = load_profiles(
        dataset_dir / "financial_profiles.csv"
    )

    events = load_events(
        dataset_dir / "financial_events.csv"
    )

    requests = load_requests(
        dataset_dir / "requests.csv"
    )

    payment_options = load_payment_options(
        dataset_dir / "request_payment_options.csv"
    )

    messages = load_messages(
        dataset_dir / "messages.csv"
    )

    exchange_rates = load_exchange_rates(
        dataset_dir / "exchange_rates.csv"
    )

    images = load_images(
        dataset_dir / "images.csv"
    )

    return Dataset(
        profiles=profiles,
        events=events,
        requests=requests,
        payment_options=payment_options,
        messages=messages,
        exchange_rates=exchange_rates,
        images=images,
    )


# ---------------------------------------------------------------------------
# SMALL QUERY HELPERS
# ---------------------------------------------------------------------------

def get_request(
    dataset: Dataset,
    request_id: str,
) -> PurchaseRequest:

    for request in dataset.requests:
        if request.request_id == request_id:
            return request

    raise KeyError(
        f"Request not found: {request_id}"
    )


def get_profile(
    dataset: Dataset,
    user_id: str,
) -> UserProfile:

    try:
        return dataset.profiles[user_id]
    except KeyError:
        raise KeyError(
            f"Financial profile not found for user: {user_id}"
        )


def get_user_events(
    dataset: Dataset,
    user_id: str,
) -> list[FinancialEvent]:

    return [
        event
        for event in dataset.events
        if event.user_id == user_id
    ]


def get_request_payment_options(
    dataset: Dataset,
    request_id: str,
) -> list[PaymentOption]:

    return [
        option
        for option in dataset.payment_options
        if option.request_id == request_id
    ]


def get_request_messages(
    dataset: Dataset,
    request_id: str,
) -> list[FinancialMessage]:

    return [
        message
        for message in dataset.messages
        if message.request_id == request_id
    ]