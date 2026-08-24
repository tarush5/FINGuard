"""Live transaction stream simulator.

Generates realistic traffic (including occasional fraud episodes) and submits it
through the real decision path, so the command centre feed, the alert queue and
the monitoring dashboards all move.

    python -m scripts.stream_producer --rate 5 --duration 120

With ``--kafka`` the events are published to ``transactions.raw`` instead, for
demonstrating the streaming topology end to end.
"""

from __future__ import annotations

import argparse
import random
import time
import uuid
from datetime import UTC, datetime

from sqlalchemy import select

from app.core.logging import configure_logging, get_logger
from app.datagen.generator import CITIES
from app.db.models.core import Customer, Merchant
from app.db.session import session_scope
from app.events.bus import event_bus
from app.events.schemas import Topic, make_event
from app.services.pipeline import TransactionInput, process_transaction, publish_pending

logger = get_logger(__name__)


def build_payload(
    customer: Customer,
    merchant: Merchant,
    rng: random.Random,
    *,
    fraudulent: bool,
) -> TransactionInput:
    now = datetime.now(UTC)
    average = float(customer.avg_transaction_amount or 3000)

    if fraudulent:
        # A takeover-shaped event: new device, foreign city, large amount.
        city, country, latitude, longitude = rng.choice(
            [c for c in CITIES if c[1] != customer.country] or CITIES
        )
        return TransactionInput(
            event_id=f"evt_{uuid.uuid4().hex}",
            transaction_id=f"TXN-{uuid.uuid4().hex[:16].upper()}",
            customer_id=customer.id,
            merchant_id=merchant.id,
            amount=round(average * rng.uniform(5, 12), 2),
            occurred_at=now,
            device_id=f"D-LIVE{uuid.uuid4().hex[:8].upper()}",
            ip_address=f"185.{rng.randint(0, 255)}.{rng.randint(0, 255)}.{rng.randint(1, 254)}",
            latitude=latitude,
            longitude=longitude,
            country=country,
            city=city,
            channel="WEB",
            merchant_category=merchant.category,
            is_demo=True,
            metadata={"source": "stream_producer", "shape": "fraud"},
        )

    return TransactionInput(
        event_id=f"evt_{uuid.uuid4().hex}",
        transaction_id=f"TXN-{uuid.uuid4().hex[:16].upper()}",
        customer_id=customer.id,
        merchant_id=merchant.id,
        amount=round(max(average * rng.lognormvariate(0, 0.45), 40), 2),
        occurred_at=now,
        device_id=f"D-LIVE{customer.id[-4:]}",
        ip_address=f"10.{rng.randint(0, 255)}.{rng.randint(0, 255)}.{rng.randint(1, 254)}",
        latitude=customer.home_latitude,
        longitude=customer.home_longitude,
        country=customer.country,
        city=customer.city,
        channel=rng.choice(["WEB", "MOBILE_APP", "POS", "API"]),
        payment_method=rng.choice(["CARD", "UPI", "NETBANKING", "WALLET"]),
        merchant_category=merchant.category,
        is_demo=True,
        metadata={"source": "stream_producer", "shape": "normal"},
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Stream synthetic transactions into FINGuard")
    parser.add_argument("--rate", type=float, default=3.0, help="transactions per second")
    parser.add_argument("--duration", type=int, default=60, help="seconds to run")
    parser.add_argument(
        "--fraud-rate", type=float, default=0.04, help="share of fraud-shaped events"
    )
    parser.add_argument(
        "--kafka",
        action="store_true",
        help="publish to transactions.raw instead of scoring inline",
    )
    args = parser.parse_args()

    configure_logging(json_output=False)
    rng = random.Random()
    interval = 1.0 / max(args.rate, 0.1)
    deadline = time.time() + args.duration
    counts = {
        "submitted": 0,
        "fraud_shaped": 0,
        "APPROVE": 0,
        "STEP_UP": 0,
        "MANUAL_REVIEW": 0,
        "DECLINE": 0,
    }

    with session_scope() as db:
        customers = list(
            db.execute(select(Customer).where(Customer.transaction_count > 3).limit(400)).scalars()
        )
        merchants = list(db.execute(select(Merchant).limit(120)).scalars())
        if not customers or not merchants:
            raise SystemExit("Seed the database first: python -m app.datagen.seed --reset")

        if args.kafka:
            event_bus.start()

        print(
            f"Streaming ~{args.rate}/s for {args.duration}s "
            f"({'kafka' if args.kafka else 'inline scoring'}). Ctrl-C to stop."
        )
        try:
            while time.time() < deadline:
                started = time.perf_counter()
                fraudulent = rng.random() < args.fraud_rate
                payload = build_payload(
                    rng.choice(customers), rng.choice(merchants), rng, fraudulent=fraudulent
                )

                if args.kafka:
                    event_bus.publish(
                        make_event(
                            Topic.TRANSACTIONS_RAW,
                            "transaction.received",
                            payload.__dict__ | {"occurred_at": payload.occurred_at.isoformat()},
                            event_id=payload.event_id,
                            partition_key=payload.customer_id,
                            producer="stream-producer",
                        )
                    )
                    counts["submitted"] += 1
                else:
                    result = process_transaction(db, payload, commit=True)
                    publish_pending([result])
                    counts["submitted"] += 1
                    counts[result.decision] = counts.get(result.decision, 0) + 1
                    if result.risk_score >= 70:
                        print(
                            f"  {result.decision:<14} risk {result.risk_score:5.1f} "
                            f"{payload.currency} {payload.amount:>12,.2f}  {payload.city}"
                        )

                counts["fraud_shaped"] += 1 if fraudulent else 0
                elapsed = time.perf_counter() - started
                time.sleep(max(interval - elapsed, 0))
        except KeyboardInterrupt:
            print("\nstopped by operator")

    print("\nsummary")
    for key, value in counts.items():
        print(f"  {key:<14} {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
