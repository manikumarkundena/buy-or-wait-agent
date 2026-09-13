"""Runnable API and CLI entrypoint for Buy or Wait."""
from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .engine.decision import decide
from .engine.forecast import forecast_next_90_days
from .engine.loader import get_request, load_dataset
from .engine.state import ExchangeRateTable

ROOT = Path(__file__).resolve().parents[1]
DATASET_DIR = ROOT / "dataset"


def build_dataset():
    return load_dataset(DATASET_DIR)


def decision_payload(dataset, request_id: str) -> dict:
    request = get_request(dataset, request_id)
    return decide(dataset, request).to_output_row()


def overview_payload(dataset) -> dict:
    profiles = list(dataset.profiles.values())
    return {"requests": len(dataset.requests), "users": len(profiles), "events": len(dataset.events), "messages": len(dataset.messages), "currencies": sorted({p.home_currency for p in profiles}), "total_balance": round(sum(p.current_available_balance for p in profiles), 2)}


def forecast_payload(dataset, user_id: str, request_id: str | None = None) -> dict:
    profile = dataset.profiles[user_id]
    request_date = get_request(dataset, request_id).request_date if request_id else min(r.request_date for r in dataset.requests if r.user_id == user_id)
    forecast = forecast_next_90_days(profile, [e for e in dataset.events if e.user_id == user_id], request_date, ExchangeRateTable(dataset.exchange_rates))
    return {"user_id": user_id, "currency": profile.home_currency, "minimum_balance": profile.minimum_balance_to_keep, "start_date": forecast.start_date.isoformat(), "end_date": forecast.end_date.isoformat(), "is_safe": forecast.is_safe, "minimum_projected_balance": round(forecast.minimum_projected_balance, 2), "ending_balance": round(forecast.ending_balance, 2), "first_unsafe_date": forecast.first_unsafe_date.isoformat() if forecast.first_unsafe_date else None, "days": [{"date": d.date.isoformat(), "balance": round(d.ending_balance, 2), "income": round(d.income, 2), "expenses": round(d.expenses, 2)} for d in forecast.days]}


def generate_output(dataset, output_path: Path) -> None:
    import csv
    rows = [decide(dataset, request).to_output_row() for request in dataset.requests]
    fieldnames = list(rows[0].keys()) if rows else []
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


class Handler(BaseHTTPRequestHandler):
    dataset = None

    def _send(self, status: int, payload: dict):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self._send(204, {})

    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        try:
            if parsed.path == "/api/health":
                return self._send(200, {"ok": True, "service": "buy-or-wait-agent"})
            if parsed.path == "/api/overview":
                return self._send(200, overview_payload(self.dataset))
            if parsed.path == "/api/requests":
                rows = [{"request_id": r.request_id, "user_id": r.user_id, "date": r.request_date.isoformat(), "amount": r.requested_amount, "type": r.request_type, "text": r.request_text} for r in self.dataset.requests]
                return self._send(200, {"requests": rows})
            if parsed.path == "/api/decision":
                request_id = query.get("request_id", [""])[0]
                return self._send(200, decision_payload(self.dataset, request_id))
            if parsed.path == "/api/forecast":
                user_id = query.get("user_id", [""])[0]
                request_id = query.get("request_id", [None])[0]
                return self._send(200, forecast_payload(self.dataset, user_id, request_id))
            return self._send(404, {"error": "Not found"})
        except Exception as exc:
            return self._send(400, {"error": str(exc), "type": type(exc).__name__})

    def log_message(self, *_args):
        return


def main():
    parser = argparse.ArgumentParser(description="Buy or Wait financial agent")
    parser.add_argument("--serve", action="store_true", help="start the local API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--output", default=str(ROOT / "output.csv"))
    args = parser.parse_args()
    dataset = build_dataset()
    if not args.serve:
        generate_output(dataset, Path(args.output))
        print(f"Generated {len(dataset.requests)} decisions -> {args.output}")
        return
    Handler.dataset = dataset
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Buy or Wait API: http://{args.host}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
