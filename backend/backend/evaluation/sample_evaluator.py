from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from backend.engine.loader import load_dataset
from backend.engine.models import PurchaseRequest
from backend.engine.decision import decide


ROOT = Path(__file__).resolve().parents[2]
DATASET_DIR = ROOT / "dataset"


OUTPUT_FIELDS = [
    "amount_safe_to_pay",
    "affordability_status",
    "recommended_payment_method",
    "payment_plan",
    "earliest_date_for_full_payment",
    "spending_changes_needed",
    "decision_explanation",
]


def to_bool(value) -> bool:
    if pd.isna(value):
        return False

    if isinstance(value, bool):
        return value

    return str(value).strip().lower() in {
        "true",
        "1",
        "yes",
        "y",
    }


def to_date(value):
    if pd.isna(value) or str(value).strip() == "":
        return None

    return date.fromisoformat(str(value).strip())


def load_sample_requests() -> pd.DataFrame:
    path = DATASET_DIR / "sample_requests.csv"

    if not path.exists():
        raise FileNotFoundError(f"Missing sample file: {path}")

    return pd.read_csv(path)


def build_request(row: pd.Series) -> PurchaseRequest:
    return PurchaseRequest(
        request_id=str(row["request_id"]),
        user_id=str(row["user_id"]),
        request_date=to_date(row["request_date"]),
        request_type=str(row["request_type"]),
        requested_amount=float(row["requested_amount"]),
        desired_completion_date=to_date(row["desired_completion_date"]),
        allows_partial_payment=to_bool(row["allows_partial_payment"]),
        request_text=str(row["request_text"]),
    )


def normalize(value):
    if pd.isna(value):
        return ""

    if isinstance(value, float):
        return round(value, 2)

    return str(value).strip()


def compare(expected, actual, field: str) -> bool:
    expected = normalize(expected)
    actual = normalize(actual)

    if field == "amount_safe_to_pay":
        try:
            return abs(float(expected) - float(actual)) <= 0.01
        except (TypeError, ValueError):
            return expected == actual

    return expected == actual


def main():
    print("=" * 70)
    print("BUY OR WAIT — SAMPLE EVALUATION")
    print("=" * 70)

    dataset = load_dataset(DATASET_DIR)
    samples = load_sample_requests()

    print(f"\nLoaded {len(samples)} sample requests.")
    print()

    total_field_matches = 0
    total_fields = 0
    request_results = []

    for _, row in samples.iterrows():
        request = build_request(row)

        try:
            decision = decide(dataset, request)

            actual = decision.to_output_row()

        except Exception as exc:
            print(f"❌ {request.request_id}: ENGINE ERROR")
            print(f"   {type(exc).__name__}: {exc}")
            print()

            request_results.append({
                "request_id": request.request_id,
                "passed": False,
                "matches": 0,
                "fields": len(OUTPUT_FIELDS),
            })

            total_fields += len(OUTPUT_FIELDS)
            continue

        mismatches = []
        matches = 0

        for field in OUTPUT_FIELDS:
            expected = row[field]
            actual_value = actual.get(field, "")

            if compare(expected, actual_value, field):
                matches += 1
            else:
                mismatches.append(
                    (
                        field,
                        normalize(expected),
                        normalize(actual_value),
                    )
                )

        total_field_matches += matches
        total_fields += len(OUTPUT_FIELDS)

        passed = len(mismatches) == 0

        status = "✅ PASS" if passed else "⚠️ REVIEW"

        print(f"{status} {request.request_id}")
        print(
            f"   Field match: "
            f"{matches}/{len(OUTPUT_FIELDS)}"
        )

        for field, expected, actual_value in mismatches:
            print(f"   • {field}")
            print(f"     expected: {expected}")
            print(f"     actual:   {actual_value}")

        print()

        request_results.append({
            "request_id": request.request_id,
            "passed": passed,
            "matches": matches,
            "fields": len(OUTPUT_FIELDS),
        })

    passed_requests = sum(
        1 for result in request_results
        if result["passed"]
    )

    accuracy = (
        total_field_matches / total_fields
        if total_fields
        else 0
    )

    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"Requests passed:   {passed_requests}/{len(samples)}")
    print(
        f"Field accuracy:    "
        f"{total_field_matches}/{total_fields} "
        f"({accuracy:.1%})"
    )

    print("\nRequest-level results:")

    for result in request_results:
        icon = "✅" if result["passed"] else "⚠️"
        print(
            f"{icon} {result['request_id']}: "
            f"{result['matches']}/{result['fields']}"
        )


if __name__ == "__main__":
    main()