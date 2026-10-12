import csv
from io import StringIO
from pathlib import Path

from portfolio_compass.sample_data_generator import (
    GeneratorConfig,
    MarketInstrument,
    OFFLINE_FALLBACK,
    generate_transactions,
    save_transactions,
    serialize_transactions,
)
from portfolio_compass.importing import normalize, read_csv, suggest_mapping
from portfolio_compass.instruments import CATALOG


def test_generation_is_repeatable_and_uses_portfolio_inputs():
    config = GeneratorConfig(count=30, shape="mixed", seed=42)
    first = generate_transactions(config, OFFLINE_FALLBACK[:3])
    second = generate_transactions(config, OFFLINE_FALLBACK[:3])

    assert first == second
    assert len(first) == 30
    assert {row["symbol"] for row in first} <= {"SPY", "QQQ", "VTI"}
    assert {row["activity"] for row in first} <= {"BUY", "SELL", "DISTRIBUTION"}
    assert all(row["company_name"] in {"SPDR S&P 500 ETF Trust", "Invesco QQQ Trust", "Vanguard Total Stock Market ETF"} for row in first)


def test_all_shapes_have_importer_aliases_and_save_csv(tmp_path: Path):
    expected = {
        "canonical": {"ticker", "date", "transaction_type", "quantity", "price"},
        "broker": {"symbol", "trade_date", "action", "shares", "unit_price"},
        "name-based": {"company_name", "transaction_date", "type", "units", "trade_price"},
        "mixed": {"symbol", "company_name", "date", "activity", "shares", "price"},
    }
    for shape, headers in expected.items():
        rows = generate_transactions(GeneratorConfig(count=8, shape=shape, seed=1), OFFLINE_FALLBACK[:3])
        parsed = list(csv.DictReader(StringIO(serialize_transactions(rows))))
        destination = save_transactions(rows, tmp_path / f"{shape}.csv")
        assert headers <= set(parsed[0])
        assert len(parsed) == 8
        assert destination.exists()


def test_generated_shapes_normalize_without_errors():
    importer_instruments = (
        MarketInstrument("AAPL", "Apple", "EQUITY", 185),
        MarketInstrument("MSFT", "Microsoft", "EQUITY", 420),
        MarketInstrument("SPY", "SPDR S&P 500 ETF Trust", "ETF", 500),
    )
    resolved = {alias: instrument for instrument in CATALOG for alias in (instrument.symbol, instrument.name)}
    for shape in ("canonical", "broker", "name-based", "mixed"):
        rows = generate_transactions(GeneratorConfig(count=16, shape=shape, seed=9), importer_instruments)
        headers, raw_rows = read_csv(serialize_transactions(rows).encode())
        transactions, issues = normalize(raw_rows, suggest_mapping(headers), resolved)
        assert len(transactions) == 16
        assert not [issue for issue in issues if issue.severity == "error"]
