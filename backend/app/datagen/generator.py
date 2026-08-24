"""Synthetic financial data generator.

Everything FINGuard demonstrates runs on data produced here.  No real person's
financial information is used anywhere in the platform.

The generator models *behaviour*, not just random rows: each customer has a home
city, a spending profile, preferred merchant categories, a device set and a
diurnal rhythm.  Fraud is then injected as coherent **episodes** (account
takeover, card testing, velocity attack, geo anomaly, merchant collusion, ring
activity, synthetic identity) so that the detection stack has real structure to
find rather than uniform noise.
"""

from __future__ import annotations

import hashlib
import math
import random
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

# --------------------------------------------------------------------- catalogues

CITIES: list[tuple[str, str, float, float]] = [
    ("Mumbai", "IN", 19.0760, 72.8777),
    ("Delhi", "IN", 28.6139, 77.2090),
    ("Bengaluru", "IN", 12.9716, 77.5946),
    ("Hyderabad", "IN", 17.3850, 78.4867),
    ("Chennai", "IN", 13.0827, 80.2707),
    ("Kolkata", "IN", 22.5726, 88.3639),
    ("Pune", "IN", 18.5204, 73.8567),
    ("Ahmedabad", "IN", 23.0225, 72.5714),
    ("Jaipur", "IN", 26.9124, 75.7873),
    ("Lucknow", "IN", 26.8467, 80.9462),
    ("Kochi", "IN", 9.9312, 76.2673),
    ("Chandigarh", "IN", 30.7333, 76.7794),
    ("Singapore", "SG", 1.3521, 103.8198),
    ("Dubai", "AE", 25.2048, 55.2708),
    ("London", "GB", 51.5074, -0.1278),
    ("New York", "US", 40.7128, -74.0060),
    ("Frankfurt", "DE", 50.1109, 8.6821),
    ("Sydney", "AU", -33.8688, 151.2093),
]

# Cities used by fraudsters far more often than by the customer base.
HIGH_RISK_CITIES = {"Dubai", "London", "New York", "Frankfurt"}

MERCHANT_CATEGORIES: list[tuple[str, str, float, float, float]] = [
    # (category, mcc, base_ticket, ticket_spread, base_fraud_rate)
    ("GROCERY", "5411", 1800, 900, 0.0015),
    ("ELECTRONICS", "5732", 24000, 18000, 0.0180),
    ("FUEL", "5541", 2400, 1100, 0.0020),
    ("TRAVEL", "4722", 32000, 22000, 0.0160),
    ("RESTAURANT", "5812", 1400, 800, 0.0018),
    ("APPAREL", "5651", 3600, 2200, 0.0060),
    ("JEWELLERY", "5944", 78000, 52000, 0.0290),
    ("GAMING", "7995", 4200, 3800, 0.0350),
    ("CRYPTO_EXCHANGE", "6051", 52000, 46000, 0.0420),
    ("STREAMING", "5815", 650, 260, 0.0030),
    ("PHARMACY", "5912", 1200, 700, 0.0012),
    ("UTILITIES", "4900", 3200, 1400, 0.0008),
    ("GIFT_CARDS", "5947", 9500, 6200, 0.0380),
    ("RIDESHARE", "4121", 480, 260, 0.0022),
    ("MARKETPLACE", "5399", 5200, 4100, 0.0090),
]

MERCHANT_PREFIXES = [
    "Aurora",
    "Vertex",
    "Blue Harbor",
    "Silverline",
    "Northwind",
    "Metro",
    "Quantum",
    "Sunrise",
    "Ironclad",
    "Lotus",
    "Cobalt",
    "Summit",
    "Riverstone",
    "Amber",
    "Pinnacle",
    "Crescent",
    "Zenith",
    "Copper",
    "Emerald",
    "Falcon",
]
MERCHANT_SUFFIXES = {
    "GROCERY": ["Mart", "Fresh", "Bazaar", "Provisions"],
    "ELECTRONICS": ["Electronics", "Tech", "Devices", "Digital"],
    "FUEL": ["Fuels", "Energy", "Petro"],
    "TRAVEL": ["Travel", "Holidays", "Voyages", "Airways"],
    "RESTAURANT": ["Kitchen", "Bistro", "Diner", "Cafe"],
    "APPAREL": ["Apparel", "Outfitters", "Threads", "Couture"],
    "JEWELLERY": ["Jewellers", "Gold", "Gems"],
    "GAMING": ["Games", "Play", "Arcade"],
    "CRYPTO_EXCHANGE": ["Exchange", "Digital Assets", "Coin"],
    "STREAMING": ["Streams", "Media", "Play"],
    "PHARMACY": ["Pharmacy", "Health", "Care"],
    "UTILITIES": ["Utilities", "Power", "Services"],
    "GIFT_CARDS": ["Gift Cards", "Vouchers", "Rewards"],
    "RIDESHARE": ["Rides", "Mobility", "Cabs"],
    "MARKETPLACE": ["Marketplace", "Store", "Traders"],
}

FIRST_NAMES = [
    "Aarav",
    "Diya",
    "Vihaan",
    "Ananya",
    "Arjun",
    "Ishita",
    "Kabir",
    "Meera",
    "Rohan",
    "Saanvi",
    "Aditya",
    "Kavya",
    "Nikhil",
    "Priya",
    "Raghav",
    "Tara",
    "Yash",
    "Zoya",
    "Imran",
    "Neha",
    "Farah",
    "Devansh",
    "Riya",
    "Manav",
    "Sneha",
    "Karan",
    "Aisha",
    "Vikram",
    "Pooja",
    "Siddharth",
    "Lakshmi",
    "Rahul",
    "Nandini",
    "Om",
    "Trisha",
]
LAST_NAMES = [
    "Sharma",
    "Verma",
    "Patel",
    "Reddy",
    "Nair",
    "Iyer",
    "Khan",
    "Bose",
    "Chopra",
    "Mehta",
    "Gupta",
    "Kulkarni",
    "Desai",
    "Rao",
    "Joshi",
    "Malhotra",
    "Sinha",
    "Banerjee",
    "Kapoor",
    "Menon",
    "Pillai",
    "Ahuja",
    "Chatterjee",
    "Bhat",
]

SEGMENTS = [("RETAIL", 0.72), ("AFFLUENT", 0.16), ("SME", 0.09), ("PRIVATE", 0.03)]
CHANNELS = [("WEB", 0.34), ("MOBILE_APP", 0.44), ("POS", 0.16), ("API", 0.06)]
PAYMENT_METHODS = [("CARD", 0.46), ("UPI", 0.32), ("NETBANKING", 0.12), ("WALLET", 0.10)]
DEVICE_TYPES = [("MOBILE", 0.66), ("DESKTOP", 0.26), ("TABLET", 0.08)]
OS_BY_TYPE = {
    "MOBILE": ["Android 14", "Android 13", "iOS 17", "iOS 18"],
    "DESKTOP": ["Windows 11", "macOS 14", "Ubuntu 24.04"],
    "TABLET": ["iPadOS 17", "Android 13"],
}
BROWSERS = ["Chrome 128", "Safari 17", "Edge 128", "Firefox 129", "FinGuard App 4.2"]

FRAUD_PATTERNS = (
    "ACCOUNT_TAKEOVER",
    "CARD_TESTING",
    "VELOCITY_ATTACK",
    "GEO_ANOMALY",
    "MERCHANT_COLLUSION",
    "FRAUD_RING",
    "SYNTHETIC_IDENTITY",
)


@dataclass
class GeneratorConfig:
    customers: int = 900
    merchants: int = 140
    transactions: int = 24_000
    fraud_rate: float = 0.011
    days: int = 90
    seed: int = 20260824
    end_at: datetime | None = None
    ring_count: int = 6
    ring_size: tuple[int, int] = (3, 7)
    currency: str = "INR"


@dataclass
class RawTransaction:
    """A generated transaction event, before it enters the platform."""

    event_id: str
    transaction_id: str
    customer_id: str
    account_id: str
    merchant_id: str
    device_id: str
    amount: float
    currency: str
    occurred_at: datetime
    payment_method: str
    merchant_category: str
    channel: str
    transaction_type: str
    ip_address: str
    latitude: float
    longitude: float
    country: str
    city: str
    session_id: str
    is_fraud: bool = False
    fraud_type: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


def _weighted(rng: random.Random, options: list[tuple[str, float]]) -> str:
    roll = rng.random()
    cumulative = 0.0
    for value, weight in options:
        cumulative += weight
        if roll <= cumulative:
            return value
    return options[-1][0]


def _jitter_location(
    rng: random.Random, lat: float, lon: float, km: float = 12.0
) -> tuple[float, float]:
    delta = km / 111.0
    return round(lat + rng.uniform(-delta, delta), 6), round(lon + rng.uniform(-delta, delta), 6)


class SyntheticDataGenerator:
    """Deterministic generator: the same seed always yields the same portfolio."""

    def __init__(self, config: GeneratorConfig | None = None) -> None:
        self.config = config or GeneratorConfig()
        self.rng = random.Random(self.config.seed)
        self.end_at = self.config.end_at or datetime.now(UTC).replace(microsecond=0)
        self.start_at = self.end_at - timedelta(days=self.config.days)
        self.customers: list[dict[str, Any]] = []
        self.accounts: list[dict[str, Any]] = []
        self.merchants: list[dict[str, Any]] = []
        self.devices: list[dict[str, Any]] = []
        self.rings: list[dict[str, Any]] = []
        self._customer_index: dict[str, dict[str, Any]] = {}
        self._merchants_by_category: dict[str, list[dict[str, Any]]] = {}
        self._attacker_ips: list[str] = []

    # ------------------------------------------------------------- reference

    def generate_reference_data(self) -> dict[str, list[dict[str, Any]]]:
        self._generate_merchants()
        self._generate_customers()
        self._generate_rings()
        return {
            "customers": self.customers,
            "accounts": self.accounts,
            "merchants": self.merchants,
            "devices": self.devices,
            "rings": self.rings,
        }

    def _generate_merchants(self) -> None:
        for index in range(self.config.merchants):
            category, mcc, ticket, spread, base_fraud = self.rng.choice(MERCHANT_CATEGORIES)
            city, country, lat, lon = self.rng.choice(CITIES)
            name = (
                f"{self.rng.choice(MERCHANT_PREFIXES)} "
                f"{self.rng.choice(MERCHANT_SUFFIXES[category])}"
            )
            # A small tail of merchants is materially riskier than the rest.
            risk_multiplier = self.rng.choices([1.0, 2.5, 6.0], weights=[0.85, 0.11, 0.04])[0]
            fraud_rate = min(base_fraud * risk_multiplier, 0.12)
            merchant = {
                "id": f"M-{10000 + index}",
                "name": name,
                "category": category,
                "mcc": mcc,
                "country": country,
                "city": city,
                "latitude": lat,
                "longitude": lon,
                "onboarded_at": self.start_at - timedelta(days=self.rng.randint(60, 1400)),
                "base_ticket": ticket,
                "ticket_spread": spread,
                "base_fraud_rate": fraud_rate,
                "high_risk_flag": fraud_rate >= 0.03,
                "risk_score": round(min(fraud_rate * 900, 96.0), 2),
            }
            self.merchants.append(merchant)
            self._merchants_by_category.setdefault(category, []).append(merchant)

    def _generate_customers(self) -> None:
        for index in range(self.config.customers):
            city, country, lat, lon = self.rng.choices(
                CITIES, weights=[6 if c[1] == "IN" else 1 for c in CITIES]
            )[0]
            segment = _weighted(self.rng, SEGMENTS)
            scale = {"RETAIL": 1.0, "AFFLUENT": 3.4, "SME": 5.2, "PRIVATE": 9.0}[segment]
            first, last = self.rng.choice(FIRST_NAMES), self.rng.choice(LAST_NAMES)
            customer_id = f"C-{80000 + index}"
            onboarded = self.start_at - timedelta(days=self.rng.randint(20, 2200))
            avg_amount = round(self.rng.uniform(1200, 6400) * scale, 2)

            preferred = self.rng.sample(
                [category for category, *_ in MERCHANT_CATEGORIES], k=self.rng.randint(3, 6)
            )
            device_count = self.rng.choices([1, 2, 3], weights=[0.55, 0.34, 0.11])[0]
            devices = [self._make_device(customer_id, n) for n in range(device_count)]

            customer = {
                "id": customer_id,
                "full_name": f"{first} {last}",
                "email": f"{first.lower()}.{last.lower()}{index}@example-bank.test",
                "phone": f"+91{self.rng.randint(7000000000, 9999999999)}",
                "national_id": f"XXXX{self.rng.randint(1000, 9999)}{self.rng.randint(1000, 9999)}",
                "segment": segment,
                "kyc_status": self.rng.choices(
                    ["VERIFIED", "PENDING", "REVIEW"], weights=[0.93, 0.05, 0.02]
                )[0],
                "country": country,
                "city": city,
                "home_latitude": lat,
                "home_longitude": lon,
                "onboarded_at": onboarded,
                "tenure_days": max((self.end_at - onboarded).days, 1),
                "avg_amount": avg_amount,
                "amount_spread": round(avg_amount * self.rng.uniform(0.25, 0.6), 2),
                "preferred_categories": preferred,
                "activity": self.rng.uniform(0.4, 2.6),  # relative transaction frequency
                "peak_hour": self.rng.choice([9, 11, 13, 15, 18, 19, 20, 21]),
                "device_ids": [d["id"] for d in devices],
                "ip_pool": [
                    f"{self.rng.randint(10, 223)}.{self.rng.randint(0, 255)}."
                    f"{self.rng.randint(0, 255)}.{self.rng.randint(1, 254)}"
                    for _ in range(self.rng.randint(1, 3))
                ],
                "risk_appetite": self.rng.random(),
            }
            self.customers.append(customer)
            self._customer_index[customer_id] = customer
            self.devices.extend(devices)

            for account_index in range(self.rng.choices([1, 2, 3], weights=[0.6, 0.3, 0.1])[0]):
                self.accounts.append(
                    {
                        "id": f"A-{customer_id[2:]}-{account_index}",
                        "customer_id": customer_id,
                        "account_type": self.rng.choice(["CHECKING", "SAVINGS", "CREDIT_CARD"]),
                        "currency": self.config.currency,
                        "masked_number": f"****{self.rng.randint(1000, 9999)}",
                        "balance": round(avg_amount * self.rng.uniform(4, 40), 2),
                        "credit_limit": round(avg_amount * self.rng.uniform(5, 30), 2),
                        "status": "ACTIVE",
                        "opened_at": onboarded,
                    }
                )

    def _make_device(self, customer_id: str, index: int) -> dict[str, Any]:
        device_type = _weighted(self.rng, DEVICE_TYPES)
        raw = f"{customer_id}:{index}:{self.rng.random()}"
        device_id = "D-" + hashlib.sha1(raw.encode()).hexdigest()[:12].upper()
        return {
            "id": device_id,
            "device_type": device_type,
            "os": self.rng.choice(OS_BY_TYPE[device_type]),
            "browser": self.rng.choice(BROWSERS),
            "fingerprint": hashlib.sha1(f"fp:{raw}".encode()).hexdigest()[:24],
            "owner": customer_id,
        }

    def _generate_rings(self) -> None:
        """Create coordinated fraud rings that share devices and IPs."""
        pool = [c["id"] for c in self.customers]
        for index in range(self.config.ring_count):
            size = self.rng.randint(*self.config.ring_size)
            members = self.rng.sample(pool, k=min(size, len(pool)))
            shared_device = "D-RING" + hashlib.sha1(f"ring{index}".encode()).hexdigest()[:8].upper()
            shared_ip = f"45.{self.rng.randint(0, 255)}.{self.rng.randint(0, 255)}.{self.rng.randint(1, 254)}"
            self.devices.append(
                {
                    "id": shared_device,
                    "device_type": "DESKTOP",
                    "os": "Windows 11",
                    "browser": "Chrome 128",
                    "fingerprint": hashlib.sha1(shared_device.encode()).hexdigest()[:24],
                    "owner": None,
                    "is_ring_device": True,
                }
            )
            mule_merchants = [
                m["id"]
                for m in self.rng.sample(
                    [m for m in self.merchants if m["high_risk_flag"]] or self.merchants,
                    k=min(2, len(self.merchants)),
                )
            ]
            self.rings.append(
                {
                    "id": f"RING-SEED-{index + 1}",
                    "members": members,
                    "device_id": shared_device,
                    "ip_address": shared_ip,
                    "merchants": mule_merchants,
                    "active_from": self.end_at - timedelta(days=self.rng.randint(3, 40)),
                }
            )

    # ---------------------------------------------------------- transactions

    def generate_transactions(self) -> Iterator[RawTransaction]:
        """Yield transactions in chronological order."""
        if not self.customers:
            self.generate_reference_data()

        legit_target = int(self.config.transactions * (1 - self.config.fraud_rate))
        events: list[RawTransaction] = []

        weights = [c["activity"] for c in self.customers]
        for _ in range(legit_target):
            customer = self.rng.choices(self.customers, weights=weights)[0]
            events.append(self._legit_transaction(customer))

        fraud_budget = self.config.transactions - legit_target
        events.extend(self._fraud_episodes(fraud_budget))

        events.sort(key=lambda txn: txn.occurred_at)
        yield from events

    def _sample_timestamp(self, customer: dict[str, Any]) -> datetime:
        """Diurnal + weekly seasonality around the customer's peak hour."""
        day_offset = self.rng.random() ** 0.7  # recent days slightly denser
        moment = self.start_at + timedelta(
            seconds=day_offset * (self.end_at - self.start_at).total_seconds()
        )
        hour = int(self.rng.gauss(customer["peak_hour"], 3.2)) % 24
        # Weekend transactions skew later in the day.
        if moment.weekday() >= 5 and self.rng.random() < 0.4:
            hour = min(23, hour + 2)
        return moment.replace(
            hour=hour, minute=self.rng.randint(0, 59), second=self.rng.randint(0, 59)
        )

    def _pick_merchant(self, customer: dict[str, Any]) -> dict[str, Any]:
        if self.rng.random() < 0.82:
            category = self.rng.choice(customer["preferred_categories"])
            pool = self._merchants_by_category.get(category)
            if pool:
                return self.rng.choice(pool)
        return self.rng.choice(self.merchants)

    def _amount_for(self, customer: dict[str, Any], merchant: dict[str, Any]) -> float:
        """Log-normal amount blending the customer profile and merchant ticket."""
        base = 0.6 * customer["avg_amount"] + 0.4 * merchant["base_ticket"]
        sigma = 0.55
        amount = base * math.exp(self.rng.gauss(0, sigma))
        return round(max(amount, 25.0), 2)

    def _customer_ip(self, customer: dict[str, Any]) -> str:
        # 88% of activity comes from the customer's known addresses; the rest is
        # genuine roaming, which is what makes the feature non-trivial.
        if self.rng.random() < 0.88:
            return self.rng.choice(customer["ip_pool"])
        return (
            f"{self.rng.randint(10, 223)}.{self.rng.randint(0, 255)}."
            f"{self.rng.randint(0, 255)}.{self.rng.randint(1, 254)}"
        )

    def _legit_transaction(self, customer: dict[str, Any]) -> RawTransaction:
        merchant = self._pick_merchant(customer)
        occurred = self._sample_timestamp(customer)
        # Most activity is near home; occasional genuine travel.
        if self.rng.random() < 0.06:
            city, country, lat, lon = self.rng.choice(CITIES)
        else:
            city, country = customer["city"], customer["country"]
            lat, lon = _jitter_location(
                self.rng, customer["home_latitude"], customer["home_longitude"]
            )
        return RawTransaction(
            event_id=f"evt_{uuid.UUID(int=self.rng.getrandbits(128)).hex}",
            transaction_id=f"TXN-{uuid.UUID(int=self.rng.getrandbits(128)).hex[:16].upper()}",
            customer_id=customer["id"],
            account_id=self._account_for(customer["id"]),
            merchant_id=merchant["id"],
            device_id=self.rng.choice(customer["device_ids"]),
            amount=self._amount_for(customer, merchant),
            currency=self.config.currency,
            occurred_at=occurred,
            payment_method=_weighted(self.rng, PAYMENT_METHODS),
            merchant_category=merchant["category"],
            channel=_weighted(self.rng, CHANNELS),
            transaction_type="PURCHASE",
            ip_address=self._customer_ip(customer),
            latitude=lat,
            longitude=lon,
            country=country,
            city=city,
            session_id=f"S-{uuid.UUID(int=self.rng.getrandbits(128)).hex[:10]}",
            is_fraud=False,
        )

    def _account_for(self, customer_id: str) -> str:
        accounts = [a["id"] for a in self.accounts if a["customer_id"] == customer_id]
        return self.rng.choice(accounts) if accounts else f"A-{customer_id[2:]}-0"

    # ------------------------------------------------------------- fraud

    def _fraud_episodes(self, budget: int) -> list[RawTransaction]:
        """Spend the fraud budget on coherent multi-transaction episodes."""
        events: list[RawTransaction] = []
        generators = {
            "ACCOUNT_TAKEOVER": self._episode_account_takeover,
            "CARD_TESTING": self._episode_card_testing,
            "VELOCITY_ATTACK": self._episode_velocity,
            "GEO_ANOMALY": self._episode_geo_anomaly,
            "MERCHANT_COLLUSION": self._episode_merchant_collusion,
            "FRAUD_RING": self._episode_ring,
            "SYNTHETIC_IDENTITY": self._episode_synthetic_identity,
        }
        pattern_weights = {
            "ACCOUNT_TAKEOVER": 0.22,
            "CARD_TESTING": 0.20,
            "VELOCITY_ATTACK": 0.15,
            "GEO_ANOMALY": 0.13,
            "MERCHANT_COLLUSION": 0.10,
            "FRAUD_RING": 0.14,
            "SYNTHETIC_IDENTITY": 0.06,
        }
        patterns = list(pattern_weights)
        weights = [pattern_weights[p] for p in patterns]

        while len(events) < budget:
            pattern = self.rng.choices(patterns, weights=weights)[0]
            produced = generators[pattern]()
            events.extend(produced[: max(budget - len(events), 0)])
        return events

    def _fraud_txn(
        self,
        customer: dict[str, Any],
        merchant: dict[str, Any],
        occurred: datetime,
        amount: float,
        fraud_type: str,
        *,
        device_id: str | None = None,
        ip_address: str | None = None,
        location: tuple[str, str, float, float] | None = None,
        channel: str = "WEB",
        session_id: str | None = None,
    ) -> RawTransaction:
        city, country, lat, lon = location or (
            customer["city"],
            customer["country"],
            *_jitter_location(self.rng, customer["home_latitude"], customer["home_longitude"]),
        )
        return RawTransaction(
            event_id=f"evt_{uuid.UUID(int=self.rng.getrandbits(128)).hex}",
            transaction_id=f"TXN-{uuid.UUID(int=self.rng.getrandbits(128)).hex[:16].upper()}",
            customer_id=customer["id"],
            account_id=self._account_for(customer["id"]),
            merchant_id=merchant["id"],
            device_id=device_id or self.rng.choice(customer["device_ids"]),
            amount=round(amount, 2),
            currency=self.config.currency,
            occurred_at=occurred,
            payment_method=self.rng.choice(["CARD", "WALLET", "UPI"]),
            merchant_category=merchant["category"],
            channel=channel,
            transaction_type="PURCHASE",
            ip_address=ip_address or self._customer_ip(customer),
            latitude=lat,
            longitude=lon,
            country=country,
            city=city,
            session_id=session_id or f"S-{uuid.UUID(int=self.rng.getrandbits(128)).hex[:10]}",
            is_fraud=True,
            fraud_type=fraud_type,
            metadata={"pattern": fraud_type},
        )

    def _new_attacker_device(self, tag: str) -> str:
        return "D-ATK" + hashlib.sha1(f"{tag}{self.rng.random()}".encode()).hexdigest()[:9].upper()

    def _attacker_ip(self) -> str:
        """Attackers reuse a small pool of hosts -- the signal detection relies on."""
        if not self._attacker_ips:
            self._attacker_ips = [
                f"185.{self.rng.randint(0, 255)}.{self.rng.randint(0, 255)}.{self.rng.randint(1, 254)}"
                for _ in range(8)
            ]
        return self.rng.choice(self._attacker_ips)

    def _episode_account_takeover(self) -> list[RawTransaction]:
        """New device + foreign location + escalating high-value cash-out."""
        customer = self.rng.choice(self.customers)
        start = self._sample_timestamp(customer)
        device = self._new_attacker_device("ato")
        ip = self._attacker_ip()
        session = f"S-ATO{self.rng.randint(10000, 99999)}"
        city, country, lat, lon = self.rng.choice(
            [c for c in CITIES if c[0] in HIGH_RISK_CITIES] or CITIES
        )
        cashout = [
            m
            for m in self.merchants
            if m["category"] in {"ELECTRONICS", "JEWELLERY", "GIFT_CARDS", "CRYPTO_EXCHANGE"}
        ]
        events = []
        for step in range(self.rng.randint(2, 5)):
            merchant = self.rng.choice(cashout or self.merchants)
            events.append(
                self._fraud_txn(
                    customer,
                    merchant,
                    start + timedelta(minutes=6 * step + self.rng.randint(0, 4)),
                    customer["avg_amount"] * self.rng.uniform(4.5, 12.0) * (1 + 0.25 * step),
                    "ACCOUNT_TAKEOVER",
                    device_id=device,
                    ip_address=ip,
                    location=(city, country, *_jitter_location(self.rng, lat, lon, 4)),
                    session_id=session,
                )
            )
        return events

    def _episode_card_testing(self) -> list[RawTransaction]:
        """Many tiny authorisations in minutes, then one large purchase."""
        customer = self.rng.choice(self.customers)
        start = self._sample_timestamp(customer)
        device = self._new_attacker_device("test")
        ip = self._attacker_ip()
        merchant = self.rng.choice(
            [m for m in self.merchants if m["category"] in {"STREAMING", "GAMING", "MARKETPLACE"}]
            or self.merchants
        )
        events = [
            self._fraud_txn(
                customer,
                merchant,
                start + timedelta(seconds=25 * step + self.rng.randint(0, 12)),
                self.rng.uniform(20, 90),
                "CARD_TESTING",
                device_id=device,
                ip_address=ip,
                channel="API",
            )
            for step in range(self.rng.randint(6, 14))
        ]
        big = self.rng.choice(self.merchants)
        events.append(
            self._fraud_txn(
                customer,
                big,
                start + timedelta(minutes=self.rng.randint(8, 20)),
                customer["avg_amount"] * self.rng.uniform(3, 9),
                "CARD_TESTING",
                device_id=device,
                ip_address=ip,
                channel="WEB",
            )
        )
        return events

    def _episode_velocity(self) -> list[RawTransaction]:
        """Rapid-fire spending spree across many merchants."""
        customer = self.rng.choice(self.customers)
        start = self._sample_timestamp(customer)
        device = self.rng.choice(customer["device_ids"])
        ip = self._customer_ip(customer)
        return [
            self._fraud_txn(
                customer,
                self.rng.choice(self.merchants),
                start + timedelta(seconds=45 * step),
                customer["avg_amount"] * self.rng.uniform(1.2, 3.5),
                "VELOCITY_ATTACK",
                device_id=device,
                ip_address=ip,
            )
            for step in range(self.rng.randint(8, 16))
        ]

    def _episode_geo_anomaly(self) -> list[RawTransaction]:
        """Two transactions minutes apart on opposite sides of the world."""
        customer = self.rng.choice(self.customers)
        start = self._sample_timestamp(customer)
        far = self.rng.choice([c for c in CITIES if c[1] != customer["country"]])
        merchant = self.rng.choice(self.merchants)
        near = self._fraud_txn(
            customer,
            merchant,
            start,
            customer["avg_amount"] * self.rng.uniform(0.8, 1.6),
            "GEO_ANOMALY",
        )
        away = self._fraud_txn(
            customer,
            self.rng.choice(self.merchants),
            start + timedelta(minutes=self.rng.randint(6, 25)),
            customer["avg_amount"] * self.rng.uniform(3.0, 8.0),
            "GEO_ANOMALY",
            device_id=self._new_attacker_device("geo"),
            ip_address=self._attacker_ip(),
            location=(far[0], far[1], *_jitter_location(self.rng, far[2], far[3], 6)),
        )
        return [near, away]

    def _episode_merchant_collusion(self) -> list[RawTransaction]:
        """One merchant repeatedly pushing large amounts from many customers."""
        merchant = self.rng.choice(
            [m for m in self.merchants if m["high_risk_flag"]] or self.merchants
        )
        start = self.start_at + timedelta(
            seconds=self.rng.random() * (self.end_at - self.start_at).total_seconds()
        )
        events = []
        for _ in range(self.rng.randint(4, 9)):
            customer = self.rng.choice(self.customers)
            events.append(
                self._fraud_txn(
                    customer,
                    merchant,
                    start + timedelta(minutes=self.rng.randint(0, 240)),
                    merchant["base_ticket"] * self.rng.uniform(2.5, 6.0),
                    "MERCHANT_COLLUSION",
                    channel="POS",
                )
            )
        return events

    def _episode_ring(self) -> list[RawTransaction]:
        """Coordinated cash-out by a ring sharing one device and IP."""
        if not self.rings:
            return self._episode_velocity()
        ring = self.rng.choice(self.rings)
        start = ring["active_from"] + timedelta(hours=self.rng.randint(0, 72))
        events = []
        for member_id in ring["members"]:
            customer = self._customer_index.get(member_id)
            if not customer:
                continue
            for _ in range(self.rng.randint(1, 3)):
                merchant_id = self.rng.choice(ring["merchants"])
                merchant = next(m for m in self.merchants if m["id"] == merchant_id)
                events.append(
                    self._fraud_txn(
                        customer,
                        merchant,
                        start + timedelta(minutes=self.rng.randint(0, 300)),
                        customer["avg_amount"] * self.rng.uniform(2.5, 7.0),
                        "FRAUD_RING",
                        device_id=ring["device_id"],
                        ip_address=ring["ip_address"],
                    )
                )
        return events

    def _episode_synthetic_identity(self) -> list[RawTransaction]:
        """A young, thin-file account escalating spend unusually fast."""
        young = [c for c in self.customers if c["tenure_days"] < 120]
        customer = self.rng.choice(young or self.customers)
        start = self.end_at - timedelta(days=self.rng.randint(0, 25))
        device = self._new_attacker_device("syn")
        return [
            self._fraud_txn(
                customer,
                self.rng.choice(self.merchants),
                start + timedelta(hours=6 * step),
                customer["avg_amount"] * (2.0 + 1.8 * step),
                "SYNTHETIC_IDENTITY",
                device_id=device,
                ip_address=self._attacker_ip(),
            )
            for step in range(self.rng.randint(3, 6))
        ]
