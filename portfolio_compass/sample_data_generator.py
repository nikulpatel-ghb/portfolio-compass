"""Generate synthetic investment transactions for Portfolio Compass.

The instrument universe is discovered from Yahoo Finance through yfinance.
Generated prices and transactions are synthetic fixtures; only the selected
instrument identities and current reference prices come from the network.
"""

from __future__ import annotations

import argparse
import csv
import random
import re
import tkinter as tk
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from tkinter import filedialog
from typing import Literal, Sequence

InputShape = Literal["canonical", "broker", "name-based", "mixed"]
SHAPES: tuple[InputShape, ...] = ("canonical", "broker", "name-based", "mixed")


@dataclass(frozen=True)
class MarketInstrument:
    symbol: str
    name: str
    quote_type: str
    reference_price: Decimal


# Used only when Yahoo is unavailable. These are deliberately broader than
# the original three-instrument demo and are never read from a sample file.
OFFLINE_FALLBACK = (
    MarketInstrument("SPY", "SPDR S&P 500 ETF Trust", "ETF", Decimal("500")),
    MarketInstrument("QQQ", "Invesco QQQ Trust", "ETF", Decimal("450")),
    MarketInstrument("VTI", "Vanguard Total Stock Market ETF", "ETF", Decimal("270")),
    MarketInstrument("NVDA", "NVIDIA", "EQUITY", Decimal("120")),
    MarketInstrument("AMD", "Advanced Micro Devices", "EQUITY", Decimal("160")),
    MarketInstrument("GOOGL", "Alphabet", "EQUITY", Decimal("180")),
    MarketInstrument("AMZN", "Amazon.com", "EQUITY", Decimal("220")),
    MarketInstrument("JPM", "JPMorgan Chase", "EQUITY", Decimal("300")),
    MarketInstrument("COST", "Costco Wholesale", "EQUITY", Decimal("950")),
    MarketInstrument("KO", "Coca-Cola", "EQUITY", Decimal("70")),
)


@dataclass(frozen=True)
class GeneratorConfig:
    """Options for a deterministic transaction fixture."""

    count: int = 24
    instrument_count: int = 3
    shape: InputShape = "canonical"
    seed: int | None = 42
    include_distributions: bool = True

    def validate(self) -> None:
        if not 3 <= self.count <= 10_000:
            raise ValueError("count must be between 3 and 10,000 transactions")
        if not 1 <= self.instrument_count <= 25:
            raise ValueError("instrument_count must be between 1 and 25")
        if self.instrument_count > self.count:
            raise ValueError("instrument_count cannot exceed the total transaction count")
        if self.shape not in SHAPES:
            raise ValueError(f"shape must be one of: {', '.join(SHAPES)}")


def _sample_instruments(candidates: Sequence[MarketInstrument], count: int, seed: int | None) -> list[MarketInstrument]:
    """Select instruments, reserving one ETF when a portfolio has 2+ holdings."""

    unique = {item.symbol: item for item in candidates}
    if len(unique) < count:
        raise RuntimeError(f"Only {len(unique)} usable instruments are available; {count} requested")
    rng = random.Random(seed)
    values = list(unique.values())
    if count == 1:
        return [rng.choice(values)]
    etfs = [item for item in values if item.quote_type == "ETF"]
    if not etfs:
        raise RuntimeError("The discovered universe contains no ETF; try again")
    selected = [rng.choice(etfs)]
    remaining = [item for item in values if item.symbol != selected[0].symbol]
    selected.extend(rng.sample(remaining, count - 1))
    return selected


def discover_instruments(count: int = 25, seed: int | None = None) -> list[MarketInstrument]:
    """Discover random US stocks/ETFs from Yahoo Finance screener results.

    yfinance's ``screen`` endpoint returns live screener quotes. We combine
    several predefined screens, remove duplicate symbols, filter to USD
    equity/ETF quotes with a usable price, then sample the requested count.
    """

    if not 1 <= count <= 100:
        raise ValueError("count must be between 1 and 100")
    try:
        import yfinance as yf

        candidates: dict[str, MarketInstrument] = {}
        for screen_name in ("most_actives", "day_gainers", "portfolio_anchors", "high_yield_bond"):
            response = yf.screen(screen_name, count=250)
            for quote in response.get("quotes", []):
                symbol = str(quote.get("symbol", "")).upper()
                quote_type = str(quote.get("quoteType", "")).upper()
                currency = str(quote.get("currency", "USD")).upper()
                raw_price = quote.get("regularMarketPrice") or quote.get("regularMarketPreviousClose")
                name = str(quote.get("longName") or quote.get("shortName") or "").strip()
                if (not re.fullmatch(r"[A-Z0-9][A-Z0-9.\-^=]{0,19}", symbol)
                        or quote_type not in {"EQUITY", "ETF"} or currency != "USD" or not name):
                    continue
                try:
                    price = Decimal(str(raw_price))
                except (ValueError, TypeError):
                    continue
                if price.is_finite() and price > 0:
                    candidates[symbol] = MarketInstrument(symbol, name, quote_type, price)
        if len(candidates) < count:
            raise RuntimeError(f"Yahoo Finance returned only {len(candidates)} usable instruments")
        return _sample_instruments(list(candidates.values()), count, seed)
    except Exception as exc:
        raise RuntimeError(f"Unable to discover instruments from Yahoo Finance: {exc}") from exc


def _business_days(start: date, end: date) -> list[date]:
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)
            if (start + timedelta(days=offset)).weekday() < 5]


def _raw_row(sequence: int, instrument: MarketInstrument, day: date, kind: str, quantity: int,
             price: Decimal | int, fees: Decimal | int, amount: Decimal | int) -> dict[str, str]:
    return {
        "ticker": instrument.symbol,
        "company": instrument.name,
        "date": day.isoformat(),
        "transaction_type": kind,
        "quantity": str(quantity),
        "price": f"{Decimal(str(price)):.2f}" if price else "",
        "fees": f"{Decimal(str(fees)):.2f}" if fees else "",
        "amount": f"{Decimal(str(amount)):.2f}" if amount else "",
        "transaction_id": f"TX-{sequence:05d}",
        "sequence": str(sequence),
    }


def _shape_row(row: dict[str, str], shape: InputShape) -> dict[str, str]:
    if shape == "canonical":
        return {"ticker": row["ticker"], "date": row["date"], "transaction_type": row["transaction_type"],
                "quantity": row["quantity"], "price": row["price"], "fees": row["fees"], "amount": row["amount"],
                "transaction_id": row["transaction_id"], "sequence": row["sequence"]}
    if shape == "broker":
        return {"symbol": row["ticker"], "trade_date": row["date"], "action": row["transaction_type"],
                "shares": row["quantity"], "unit_price": row["price"], "commission": row["fees"],
                "cash_amount": row["amount"], "trade_id": row["transaction_id"], "order": row["sequence"]}
    if shape == "name-based":
        return {"company_name": row["company"], "transaction_date": row["date"], "type": row["transaction_type"],
                "units": row["quantity"], "trade_price": row["price"], "fees": row["fees"],
                "amount": row["amount"], "id": row["transaction_id"], "order": row["sequence"]}
    return {"symbol": row["ticker"], "company_name": row["company"], "date": row["date"],
            "activity": row["transaction_type"], "shares": row["quantity"], "price": row["price"],
            "fees": row["fees"], "cash_amount": row["amount"], "id": row["transaction_id"],
            "sequence_number": row["sequence"]}


def _transactions(config: GeneratorConfig, instruments: Sequence[MarketInstrument]) -> list[dict[str, str]]:
    rng = random.Random(config.seed)
    days = _business_days(date.today() - timedelta(days=900), date.today() - timedelta(days=1))
    selected = list(instruments)
    rng.shuffle(selected)
    held = {item.symbol: 0 for item in selected}
    rows: list[dict[str, str]] = []
    sequence = 1

    for instrument in selected:
        quantity = rng.choice((8, 10, 12, 15))
        price = instrument.reference_price * Decimal(str(rng.uniform(0.90, 1.10))).quantize(Decimal("0.01"))
        rows.append(_raw_row(sequence, instrument, days.pop(0), "BUY", quantity, price, 0, 0))
        held[instrument.symbol] = quantity
        sequence += 1

    while len(rows) < config.count:
        instrument = rng.choice(selected)
        day = days.pop(rng.randrange(len(days)))
        price = instrument.reference_price * Decimal(str(rng.uniform(0.88, 1.16))).quantize(Decimal("0.01"))
        roll = rng.random()
        if config.include_distributions and roll < 0.16:
            amount = Decimal(str(rng.choice(("0.22", "0.25", "0.80")))) * held[instrument.symbol]
            rows.append(_raw_row(sequence, instrument, day, "DISTRIBUTION", 0, 0, 0, amount))
        elif roll < 0.52:
            quantity = rng.choice((2, 3, 5, 7))
            rows.append(_raw_row(sequence, instrument, day, "BUY", quantity, price, 0, 0))
            held[instrument.symbol] += quantity
        elif held[instrument.symbol] > 2:
            quantity = min(rng.choice((1, 2, 3, 4)), held[instrument.symbol] - 1)
            fees = Decimal("4.95") if rng.random() < 0.25 else Decimal("0")
            rows.append(_raw_row(sequence, instrument, day, "SELL", quantity, price, fees, 0))
            held[instrument.symbol] -= quantity
        else:
            quantity = rng.choice((2, 3, 5))
            rows.append(_raw_row(sequence, instrument, day, "BUY", quantity, price, 0, 0))
            held[instrument.symbol] += quantity
        sequence += 1
    rows.sort(key=lambda row: (row["date"], int(row["sequence"])))
    return rows


def generate_transactions(config: GeneratorConfig = GeneratorConfig(),
                           instruments: Sequence[MarketInstrument] | None = None) -> list[dict[str, str]]:
    """Generate valid transactions using online-discovered instruments by default."""

    config.validate()
    selected = list(instruments) if instruments is not None else discover_instruments(config.instrument_count, config.seed)
    selected = _sample_instruments(selected, config.instrument_count, config.seed)
    return [_shape_row(row, config.shape) for row in _transactions(config, selected)]


def serialize_transactions(rows: list[dict[str, str]]) -> str:
    if not rows:
        return ""
    from io import StringIO

    output = StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=list(rows[0]), lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


def choose_output_path(shape: InputShape) -> Path | None:
    root = tk.Tk()
    root.withdraw()
    try:
        selected = filedialog.asksaveasfilename(
            title=f"Save Portfolio Compass {shape} sample",
            defaultextension=".csv",
            initialfile=f"portfolio_compass_{shape.replace('-', '_')}.csv",
            filetypes=[("CSV files", "*.csv")],
            confirmoverwrite=True,
        )
    finally:
        root.destroy()
    return Path(selected) if selected else None


def save_transactions(rows: list[dict[str, str]], destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(serialize_transactions(rows), encoding="utf-8", newline="")
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate synthetic stock/ETF transaction CSV input for Portfolio Compass.")
    parser.add_argument("--count", type=int, default=None, help="number of transactions (3-10,000); otherwise prompt")
    parser.add_argument("--instruments", type=int, default=None, help="number of stocks/investments (1-25); otherwise prompt")
    parser.add_argument("--shape", choices=SHAPES, default="canonical")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--no-distributions", action="store_true")
    parser.add_argument("--offline", action="store_true", help="use the broader built-in fallback universe")
    parser.add_argument("--output", type=Path, help="output CSV path; omit to open Save As")
    args = parser.parse_args()
    instrument_count = args.instruments if args.instruments is not None else _ask_count(
        "Number of Stocks/Investments", 3, 1, 25)
    transaction_count = args.count if args.count is not None else _ask_count(
        "Number of transactions (total)", 24, instrument_count, 10_000)
    config = GeneratorConfig(transaction_count, instrument_count, args.shape, args.seed, not args.no_distributions)
    instruments = _sample_instruments(OFFLINE_FALLBACK, instrument_count, args.seed) if args.offline else None
    source = "offline fallback" if args.offline else "Yahoo Finance"
    if instruments is None:
        try:
            instruments = discover_instruments(instrument_count, args.seed)
        except RuntimeError as exc:
            print(f"Online discovery unavailable ({exc}); using the labeled offline fallback.")
            instruments = _sample_instruments(OFFLINE_FALLBACK, instrument_count, args.seed)
            source = "offline fallback"
    rows = generate_transactions(config, instruments)
    destination = args.output or choose_output_path(config.shape)
    if destination is None:
        print("Save cancelled; no file was written.")
        return
    save_transactions(rows, destination)
    print(f"Saved {len(rows):,} synthetic transactions from {source} to {destination}")


def _ask_count(label: str, default: int, minimum: int, maximum: int) -> int:
    while True:
        raw = input(f"{label} [{default}]: ").strip()
        if not raw:
            return default
        try:
            value = int(raw)
        except ValueError:
            value = 0
        if minimum <= value <= maximum:
            return value
        print(f"Enter a whole number from {minimum:,} to {maximum:,}.")


if __name__ == "__main__":
    main()
