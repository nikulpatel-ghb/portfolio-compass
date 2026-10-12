"""Bounded CSV parsing. No silent row deletion, numeric coercion or date guessing."""

import csv
import io
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from .models import MAX_BYTES, MAX_INSTRUMENTS, MAX_ROWS, Issue, Transaction, ZERO

ALIASES = {
    'instrument': ['code', 'stockcode', 'securitycode', 'ticker', 'symbol', 'stock', 'company', 'companyname', 'investment', 'security', 'fundname', 'name'],
    'name': ['companyname', 'securityname', 'fundname', 'name'],
    'date': ['date', 'tradedate', 'transactiondate', 'postingdate'],
    'type': ['type', 'transactiontype', 'action', 'side', 'activity'],
    'quantity': ['quantity', 'qty', 'shares', 'units'],
    'price': ['price', 'unitprice', 'shareprice', 'tradeprice'],
    'amount': ['cashamount', 'amount', 'dividend', 'distributionamount'],
    'fees': ['fees', 'fee', 'commission', 'commissions'],
    'id': ['id', 'transactionid', 'tradeid'],
    'sequence': ['sequence', 'order', 'sequencenumber'],
}
TYPES = {'BUY': 'BUY', 'BOUGHT': 'BUY', 'PURCHASE': 'BUY', 'B': 'BUY',
         'SELL': 'SELL', 'SOLD': 'SELL', 'SALE': 'SELL', 'S': 'SELL',
         'DIVIDEND': 'DISTRIBUTION', 'DIV': 'DISTRIBUTION', 'DISTRIBUTION': 'DISTRIBUTION',
         'CASH DIVIDEND': 'DISTRIBUTION'}


def read_csv(data: bytes, encoding='utf-8-sig', delimiter='Auto'):
    if not data or len(data) > MAX_BYTES:
        raise ValueError('Upload a nonempty CSV no larger than 10 MB.')
    try:
        text = data.decode(encoding)
    except UnicodeDecodeError as exc:
        raise ValueError('This encoding could not read the file. Choose its encoding explicitly.') from exc
    if '\x00' in text:
        raise ValueError('Binary content is not a supported CSV.')
    if delimiter == 'Auto':
        try:
            delimiter = csv.Sniffer().sniff(text[:8192], delimiters=',;\t|').delimiter
        except csv.Error:
            delimiter = ','
    reader = csv.reader(io.StringIO(text), delimiter=delimiter, strict=True)
    try:
        headers = next(reader)
        headers = [h.strip() for h in headers]
        if len(headers) > 40 or len(headers) < 2 or any(not h or len(h) > 100 for h in headers):
            raise ValueError('Use 2–40 nonempty column names, each at most 100 characters.')
        if len(set(headers)) != len(headers):
            raise ValueError('Column names must be unique.')
        rows = []
        for row in reader:
            if not row or all(not cell.strip() for cell in row):
                continue
            if len(row) != len(headers) or any(len(cell) > 500 for cell in row):
                raise ValueError(f'CSV record ending on line {reader.line_num} has invalid columns or oversized cells.')
            rows.append({**dict(zip(headers, (cell.strip() for cell in row))), '_row': reader.line_num})
            if len(rows) > MAX_ROWS:
                raise ValueError('The limit is 10,000 transactions.')
    except (csv.Error, StopIteration) as exc:
        raise ValueError('The CSV structure could not be read.') from exc
    if not rows:
        raise ValueError('The CSV contains no transaction rows.')
    return headers, rows


def suggest_mapping(headers):
    clean = {re.sub(r'[^a-z0-9]', '', h.lower()): h for h in headers}
    result = {field: next((clean[a] for a in aliases if a in clean), '') for field, aliases in ALIASES.items()}
    if result['name'] == result['instrument']:
        result['name'] = ''
    return result


def number(value, convention='US', default=None):
    if value == '' and default is not None:
        return default
    text = str(value).strip().removeprefix('$').strip()
    pattern = r'\+?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?' if convention == 'US' else r'\+?(?:\d{1,3}(?:\.\d{3})+|\d+)(?:,\d+)?'
    if not re.fullmatch(pattern, text):
        raise ValueError('Use positive numeric values in the selected number format; encode direction in transaction type.')
    text = text.replace(',', '') if convention == 'US' else text.replace('.', '').replace(',', '.')
    try:
        value = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError('Invalid numeric value.') from exc
    if not value.is_finite() or value > Decimal('1000000000000') or value.as_tuple().exponent < -10:
        raise ValueError('Numeric value exceeds supported bounds (1 trillion; 10 decimal places).')
    return value


def parse_day(text, date_format):
    formats = {'ISO (YYYY-MM-DD)': '%Y-%m-%d', 'US (MM/DD/YYYY)': '%m/%d/%Y', 'Day first (DD/MM/YYYY)': '%d/%m/%Y'}
    try:
        day = datetime.strptime(text, formats[date_format]).date()
    except (ValueError, KeyError) as exc:
        raise ValueError('Date does not match the selected format.') from exc
    if day > date.today() or day < date(1990, 1, 1):
        raise ValueError('Dates must be between 1990-01-01 and today.')
    return day


def normalize(rows, mapping, resolved, date_format='ISO (YYYY-MM-DD)', convention='US', keep_duplicates=False):
    issues, result = [], []
    if any(not mapping.get(k) for k in ['instrument', 'date', 'type']):
        return [], [Issue('error', 'Map the investment, date, and transaction type columns.')]
    columns = [v for v in mapping.values() if v]
    if len(columns) != len(set(columns)):
        return [], [Issue('error', 'Each input column can map to only one field.')]
    seen_ids, seen_rows = set(), set()
    for row in rows:
        def get(key):
            return row.get(mapping.get(key, ''), '')
        try:
            original = get('instrument')
            instrument = resolved.get(original)
            if instrument is None:
                raise ValueError('Confirm the investment identity before analysis.')
            if mapping.get('name') and get('name'):
                other = resolved.get(get('name'))
                if other is None or other.symbol != instrument.symbol:
                    raise ValueError('Ticker and company/fund name do not resolve to the same security.')
            kind = TYPES.get(get('type').upper())
            if not kind:
                raise ValueError('Unsupported transaction type. Use BUY, SELL or DISTRIBUTION.')
            day = parse_day(get('date'), date_format)
            trade = kind != 'DISTRIBUTION'
            qty = number(get('quantity'), convention) if trade else ZERO
            price = number(get('price'), convention) if trade else ZERO
            amount = number(get('amount'), convention) if not trade else ZERO
            fees = number(get('fees'), convention, ZERO)
            if (trade and (qty <= 0 or price <= 0)) or (not trade and amount <= 0):
                raise ValueError('Trade quantity/price and distribution amount must be greater than zero.')
            if trade and kind == 'SELL' and fees > qty * price:
                raise ValueError('Sale fees cannot exceed proceeds.')
            if not trade and fees:
                raise ValueError('For distributions supply the net cash amount with zero separate fees.')
            identifier = get('id') or f'row-{row["_row"]}'
            if identifier in seen_ids:
                raise ValueError('Duplicate transaction ID; correct it rather than silently dropping a row.')
            seen_ids.add(identifier)
            signature = (instrument.symbol, day, kind, qty, price, fees, amount)
            if signature in seen_rows:
                issues.append(Issue('warning' if keep_duplicates else 'error', 'Possible duplicate trade/distribution. Confirm that identical rows are intentional.', row['_row']))
            seen_rows.add(signature)
            result.append(Transaction(identifier, day, instrument.symbol, kind, qty, price, fees, amount, row['_row'], original, number(get('sequence'), convention, ZERO)))
        except ValueError as exc:
            issues.append(Issue('error', str(exc), row['_row']))
    if len({t.symbol for t in result}) > MAX_INSTRUMENTS:
        issues.append(Issue('error', 'The limit is 100 resolved portfolio investments.'))
    if not mapping.get('fees'):
        issues.append(Issue('warning', 'No fee column: fees are assumed to be zero.'))
    if not mapping.get('sequence') and len({(t.day, t.symbol) for t in result}) < len(result):
        issues.append(Issue('warning', 'Same-day events use original file order. Map a sequence column if needed.'))
    return sorted(result, key=lambda t: (t.day, t.sequence, t.row)), issues


def export_csv(records):
    if not records:
        return b''
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=list(records[0]))
    writer.writeheader()
    for record in records:
        safe = {}
        for key, value in record.items():
            if isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@', '\t', '\r')):
                value = "'" + value
            safe[key] = value
        writer.writerow(safe)
    return output.getvalue().encode('utf-8-sig')
