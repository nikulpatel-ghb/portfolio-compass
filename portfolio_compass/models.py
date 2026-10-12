"""Value objects shared by the UI, accounting and provider boundaries."""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

ZERO = Decimal('0')
MAX_ROWS = 10_000
MAX_INSTRUMENTS = 100
MAX_BYTES = 10 * 1024 * 1024


@dataclass(frozen=True)
class Instrument:
    symbol: str
    name: str
    kind: str = 'Stock'
    exchange: str = 'NASDAQ'
    currency: str = 'USD'
    aliases: tuple[str, ...] = ()
    sector: str = 'Unknown'
    source: str = 'Verified catalog'

    @property
    def label(self):
        return f'{self.name} ({self.symbol})'


@dataclass(frozen=True)
class Transaction:
    id: str
    day: date
    symbol: str
    kind: str
    quantity: Decimal = ZERO
    price: Decimal = ZERO
    fees: Decimal = ZERO
    amount: Decimal = ZERO
    row: int = 0
    original: str = ''
    sequence: Decimal = ZERO


@dataclass(frozen=True)
class Issue:
    severity: str
    message: str
    row: int = 0


@dataclass(frozen=True)
class Action:
    id: str
    day: date
    symbol: str
    kind: str
    value: Decimal


@dataclass
class MarketData:
    prices: dict[str, dict[date, Decimal]] = field(default_factory=dict)
    actions: list[Action] = field(default_factory=list)
    benchmark: dict[date, Decimal] = field(default_factory=dict)
    issues: list[Issue] = field(default_factory=list)
    income_complete: bool = True
    demo: bool = False
    as_of: date | None = None


@dataclass
class Analysis:
    transactions: list[Transaction]
    instruments: dict[str, Instrument]
    holdings: list[dict]
    daily: list[dict]
    income: list[dict]
    issues: list[Issue]
    conflicts: list[dict]
    cashflows: list[tuple[date, float]]
    market: MarketData

    @property
    def complete(self):
        return bool(self.daily) and self.daily[-1]['complete'] and not self.conflicts
