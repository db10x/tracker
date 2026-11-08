import argparse
import csv
import importlib
import os
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
TRANSACTION_SOURCE = "degiro_account_csv"
BROKER_NAME = "Degiro"
MONEY_ZERO = Decimal("0")
PURCHASE_PATTERN = re.compile(
    r"^Acquisto\s+([\d.,]+)\s+.+?@([\d.,]+)\s*EUR\b", re.IGNORECASE
)
TRANSFER_PATTERN = re.compile(
    r"^TRASFERIMENTO IN USCITA\b.*?\bVendita\s+([\d.,]+)", re.IGNORECASE
)
UNIT_PRICE_PATTERN = re.compile(r"@([\d.,]+)\s*EUR\b", re.IGNORECASE)
IMPORT_REFERENCE_PATTERN = re.compile(r"^import_ref=(.+?):rows=([\d,]+)\s*\|")


@dataclass(frozen=True)
class StatementRow:
    number: int
    transaction_date: date | None
    description: str
    isin: str
    amount: Decimal | None
    order_id: str


@dataclass(frozen=True)
class Transaction:
    transaction_date: date
    transaction_type: str
    ticker: str | None
    quantity: Decimal | None
    unit_price: Decimal | None
    gross_amount: Decimal
    fees: Decimal
    net_cash_amount: Decimal | None
    import_reference: str
    notes: str
    source_rows: tuple[int, ...]


@dataclass(frozen=True)
class UnsupportedRow:
    number: int
    description: str
    amount: Decimal | None
    transaction_date: date | None


def parse_euro_amount(value: str) -> Decimal | None:
    value = value.strip()
    if not value:
        return None
    if "," in value:
        value = value.replace(".", "").replace(",", ".")
    try:
        amount = Decimal(value)
    except InvalidOperation as error:
        raise ValueError(f"Importo non valido: {value!r}") from error
    if not amount.is_finite():
        raise ValueError(f"Importo non finito: {value!r}")
    return amount


def load_tickers(anagrafica_path: Path) -> dict[str, str]:
    with anagrafica_path.open(encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file, delimiter=";")
        if not {"ticker", "isin"}.issubset(reader.fieldnames or ()):
            raise ValueError(
                f"{anagrafica_path} deve contenere le colonne 'ticker' e 'isin'."
            )
        tickers: dict[str, str] = {}
        for line_number, row in enumerate(reader, 2):
            isin = (row["isin"] or "").strip().upper()
            ticker = (row["ticker"] or "").strip()
            if not isin or not ticker:
                continue
            previous_ticker = tickers.get(isin)
            if previous_ticker and previous_ticker != ticker:
                raise ValueError(
                    f"ISIN duplicato con ticker diversi in {anagrafica_path}, "
                    f"riga {line_number}: {isin}."
                )
            tickers[isin] = ticker
    return tickers


def _column_index(header: list[str], name: str) -> int:
    matches = [index for index, value in enumerate(header) if value.strip() == name]
    if len(matches) != 1:
        raise ValueError(
            f"Il CSV deve contenere una sola colonna {name!r}; "
            f"trovate {len(matches)}."
        )
    return matches[0]


def read_statement(path: Path) -> list[StatementRow]:
    with path.open(encoding="utf-8-sig", newline="") as file:
        reader = csv.reader(file)
        try:
            header = next(reader)
        except StopIteration as error:
            raise ValueError(f"Il file {path} è vuoto.") from error

        indexes = {
            name: _column_index(header, name)
            for name in ("Data", "ISIN", "Descrizione", "Variazioni", "ID Ordine")
        }
        amount_index = indexes["Variazioni"] + 1
        if amount_index >= len(header):
            raise ValueError("Manca la colonna numerica dopo 'Variazioni'.")

        statement_rows = []
        for line_number, values in enumerate(reader, 2):
            if not values or not any(value.strip() for value in values):
                continue

            def value_at(index: int) -> str:
                return values[index].strip() if index < len(values) else ""

            currency = value_at(indexes["Variazioni"])
            if currency and currency.upper() != "EUR":
                raise ValueError(
                    f"Valuta non supportata alla riga {line_number}: {currency!r}."
                )

            raw_amount = value_at(amount_index)
            try:
                amount = parse_euro_amount(raw_amount)
            except ValueError as error:
                raise ValueError(f"Riga {line_number}: {error}") from error

            raw_date = value_at(indexes["Data"])
            if raw_date:
                try:
                    transaction_date = datetime.strptime(
                        raw_date, "%d-%m-%Y"
                    ).date()
                except ValueError as error:
                    raise ValueError(
                        f"Data non valida alla riga {line_number}: {raw_date!r}."
                    ) from error
            else:
                transaction_date = None

            statement_rows.append(
                StatementRow(
                    number=line_number,
                    transaction_date=transaction_date,
                    description=value_at(indexes["Descrizione"]),
                    isin=value_at(indexes["ISIN"]).upper(),
                    amount=amount,
                    order_id=value_at(indexes["ID Ordine"]),
                )
            )
    return statement_rows


def _ticker_for_isin(isin: str, tickers: dict[str, str], row_number: int) -> str:
    ticker = tickers.get(isin.upper())
    if not ticker:
        raise ValueError(
            f"ISIN {isin!r} non presente in Anagrafica.csv (riga {row_number})."
        )
    return ticker


def _import_reference(filename: str, rows: tuple[int, ...]) -> str:
    row_numbers = ",".join(str(number) for number in sorted(rows))
    return f"{filename}:rows={row_numbers}"


def _make_transaction(
    *,
    filename: str,
    rows: tuple[StatementRow, ...],
    transaction_type: str,
    ticker: str | None,
    quantity: Decimal | None,
    unit_price: Decimal | None,
    gross_amount: Decimal,
    fees: Decimal = MONEY_ZERO,
    net_cash_amount: Decimal | None,
    order_id: str = "",
    note_prefix: str = "",
) -> Transaction:
    source_rows = tuple(sorted(row.number for row in rows))
    reference = _import_reference(filename, source_rows)
    descriptions = " / ".join(row.description for row in rows)
    transaction_date = rows[0].transaction_date
    if transaction_date is None:
        raise ValueError(
            f"Data mancante nei movimenti importati alle righe {source_rows}."
        )
    details = [
        f"import_ref={reference}",
        *([f"ID Ordine={order_id}"] if order_id else []),
        descriptions,
    ]
    if note_prefix:
        details.append(note_prefix)
    return Transaction(
        transaction_date=transaction_date,
        transaction_type=transaction_type,
        ticker=ticker,
        quantity=quantity,
        unit_price=unit_price,
        gross_amount=gross_amount,
        fees=fees,
        net_cash_amount=net_cash_amount,
        import_reference=reference,
        notes=" | ".join(details),
        source_rows=source_rows,
    )


def build_import_plan(
    statement_path: Path, anagrafica_path: Path
) -> tuple[list[Transaction], int, list[UnsupportedRow]]:
    tickers = load_tickers(anagrafica_path)
    rows = read_statement(statement_path)
    buys_by_order: dict[str, list[tuple[StatementRow, Decimal, Decimal]]] = defaultdict(
        list
    )
    commissions_by_order: dict[str, list[StatementRow]] = defaultdict(list)
    transactions: list[Transaction] = []
    skipped_rows = 0
    unsupported_rows: list[UnsupportedRow] = []

    for row in rows:
        description = row.description.strip()
        normalized = description.casefold()

        if (
            row.transaction_date is None
            and row.amount is None
            and not row.isin
            and not row.order_id
        ):
            skipped_rows += 1
            continue

        if normalized.startswith("cambio di prodotto"):
            skipped_rows += 1
            continue

        if normalized.startswith("degiro cash sweep transfer"):
            skipped_rows += 1
            continue

        transfer_match = TRANSFER_PATTERN.search(description)
        if transfer_match:
            quantity = parse_euro_amount(transfer_match.group(1))
            if quantity is None or quantity <= MONEY_ZERO:
                raise ValueError(
                    f"Quantità non valida nel trasferimento alla riga {row.number}."
                )
            ticker = _ticker_for_isin(row.isin, tickers, row.number)
            price_match = UNIT_PRICE_PATTERN.search(description)
            unit_price = (
                parse_euro_amount(price_match.group(1)) if price_match else None
            )
            if unit_price is not None and unit_price <= MONEY_ZERO:
                raise ValueError(
                    f"Prezzo non valido nel trasferimento alla riga {row.number}."
                )
            gross_amount = quantity * unit_price if unit_price is not None else MONEY_ZERO
            transactions.append(
                _make_transaction(
                    filename=statement_path.name,
                    rows=(row,),
                    transaction_type="TRANSFER_OUT",
                    ticker=ticker,
                    quantity=quantity,
                    unit_price=unit_price,
                    gross_amount=gross_amount,
                    net_cash_amount=None,
                )
            )
            continue

        if row.amount is None or row.amount == MONEY_ZERO:
            ignored_zero_prefixes = (
                "deposito flatex",
                "acquisto",
                "degiro commissioni di transazione",
                "degiro costi di transazione",
                "degiro costi di connessione",
                "flatex interest",
                "degiro courtesy",
                "trasferisci dal tuo conto deposito",
                "trasferisci sul tuo conto deposito",
            )
            if description and not normalized.startswith(ignored_zero_prefixes):
                unsupported_rows.append(
                    UnsupportedRow(
                        number=row.number,
                        description=description,
                        amount=row.amount,
                        transaction_date=row.transaction_date,
                    )
                )
            skipped_rows += 1
            continue

        if normalized.startswith("deposito flatex"):
            if row.amount <= MONEY_ZERO:
                raise ValueError(
                    f"Il deposito deve essere un accredito positivo "
                    f"(riga {row.number})."
                )
            transactions.append(
                _make_transaction(
                    filename=statement_path.name,
                    rows=(row,),
                    transaction_type="DEPOSIT",
                    ticker=None,
                    quantity=None,
                    unit_price=None,
                    gross_amount=row.amount,
                    net_cash_amount=row.amount,
                )
            )
            continue

        purchase_match = PURCHASE_PATTERN.search(description)
        if purchase_match:
            if row.amount >= MONEY_ZERO:
                raise ValueError(
                    f"L'acquisto deve avere una variazione negativa "
                    f"(riga {row.number})."
                )
            quantity = parse_euro_amount(purchase_match.group(1))
            unit_price = parse_euro_amount(purchase_match.group(2))
            if (
                quantity is None
                or quantity <= MONEY_ZERO
                or unit_price is None
                or unit_price <= MONEY_ZERO
            ):
                raise ValueError(
                    f"Quantità o prezzo non validi nell'acquisto "
                    f"(riga {row.number})."
                )
            ticker = _ticker_for_isin(row.isin, tickers, row.number)
            order_key = row.order_id or f"row-{row.number}"
            buys_by_order[order_key].append((row, quantity, unit_price))
            continue

        if normalized.startswith(
            ("degiro commissioni di transazione", "degiro costi di transazione")
        ):
            if not row.order_id:
                raise ValueError(
                    f"Commissione senza ID Ordine alla riga {row.number}."
                )
            if row.amount >= MONEY_ZERO:
                raise ValueError(
                    f"La commissione deve essere negativa "
                    f"(riga {row.number})."
                )
            commissions_by_order[row.order_id].append(row)
            continue

        if normalized.startswith(
            ("degiro costi di connessione", "flatex interest", "degiro courtesy")
        ):
            is_cashback = normalized.startswith("degiro courtesy")
            transactions.append(
                _make_transaction(
                    filename=statement_path.name,
                    rows=(row,),
                    transaction_type="FEE",
                    ticker=None,
                    quantity=None,
                    unit_price=None,
                    gross_amount=abs(row.amount),
                    net_cash_amount=row.amount,
                    note_prefix="Cashback DEGIRO courtesy" if is_cashback else "",
                )
            )
            continue

        unsupported_rows.append(
            UnsupportedRow(
                number=row.number,
                description=description or "(descrizione vuota)",
                amount=row.amount,
                transaction_date=row.transaction_date,
            )
        )
        skipped_rows += 1

    unmatched_commissions = set(commissions_by_order) - set(buys_by_order)
    if unmatched_commissions:
        order_id = sorted(unmatched_commissions)[0]
        raise ValueError(
            f"Commissione con ID Ordine {order_id!r} senza acquisto corrispondente."
        )

    for order_id, purchases in buys_by_order.items():
        purchase_rows = [purchase[0] for purchase in purchases]
        ticker_values = {
            _ticker_for_isin(row.isin, tickers, row.number) for row in purchase_rows
        }
        dates = {row.transaction_date for row in purchase_rows}
        if len(ticker_values) != 1 or len(dates) != 1:
            raise ValueError(
                f"L'ID Ordine {order_id!r} contiene acquisti di ticker o date "
                "diversi e non può essere aggregato."
            )

        commission_rows = commissions_by_order.get(order_id, [])
        purchase_isins = {row.isin for row in purchase_rows}
        for commission in commission_rows:
            if commission.isin and commission.isin not in purchase_isins:
                raise ValueError(
                    f"ISIN della commissione non corrispondente all'acquisto "
                    f"(riga {commission.number}, ID Ordine {order_id})."
                )

        quantity = sum((item[1] for item in purchases), MONEY_ZERO)
        gross_amount = sum((-item[0].amount for item in purchases), MONEY_ZERO)
        unit_price = sum(
            (item[1] * item[2] for item in purchases), MONEY_ZERO
        ) / quantity
        fees = sum((abs(row.amount) for row in commission_rows), MONEY_ZERO)
        net_cash_amount = sum(
            (item[0].amount for item in purchases), MONEY_ZERO
        ) + sum((row.amount for row in commission_rows), MONEY_ZERO)
        all_rows = tuple(purchase_rows + commission_rows)
        transactions.append(
            _make_transaction(
                filename=statement_path.name,
                rows=all_rows,
                transaction_type="BUY",
                ticker=next(iter(ticker_values)),
                quantity=quantity,
                unit_price=unit_price,
                gross_amount=gross_amount,
                fees=fees,
                net_cash_amount=net_cash_amount,
                order_id="" if order_id.startswith("row-") else order_id,
            )
        )

    transactions.sort(key=lambda transaction: transaction.source_rows[0])
    return transactions, skipped_rows, unsupported_rows


def _transaction_values(
    transaction: Transaction, portfolio_id: int, broker_id: int
) -> tuple[object, ...]:
    return (
        portfolio_id,
        broker_id,
        transaction.ticker,
        transaction.transaction_type,
        transaction.transaction_date,
        transaction.quantity,
        transaction.unit_price,
        transaction.gross_amount,
        transaction.fees,
        MONEY_ZERO,
        transaction.net_cash_amount,
        TRANSACTION_SOURCE,
        transaction.notes,
    )


def _reference_from_notes(notes: str | None) -> str | None:
    if not notes:
        return None
    match = IMPORT_REFERENCE_PATTERN.search(notes)
    if not match:
        return None
    return f"{match.group(1)}:rows={match.group(2)}"


def _validate_database_targets(
    cursor, portfolio_id: int, transactions: list[Transaction]
) -> int:
    cursor.execute("SELECT id FROM public.portfolios WHERE id = %s", (portfolio_id,))
    if cursor.fetchone() is None:
        raise ValueError(f"Portafoglio con ID {portfolio_id} non trovato.")

    cursor.execute(
        "SELECT id FROM public.brokers WHERE name = %s",
        (BROKER_NAME,),
    )
    broker_rows = cursor.fetchall()
    if not broker_rows:
        raise ValueError(f"Broker {BROKER_NAME!r} non trovato in public.brokers.")
    if len(broker_rows) > 1:
        raise ValueError(
            f"Trovati più broker con nome {BROKER_NAME!r} in public.brokers."
        )
    broker_id = broker_rows[0][0]

    tickers = sorted(
        {transaction.ticker for transaction in transactions if transaction.ticker}
    )
    cursor.execute(
        "SELECT ticker FROM public.instruments WHERE ticker = ANY(%s)",
        (tickers,),
    )
    available_tickers = {row[0] for row in cursor.fetchall()}
    missing_tickers = sorted(set(tickers) - available_tickers)
    if missing_tickers:
        raise ValueError(
            "Ticker non presenti in public.instruments: "
            + ", ".join(missing_tickers)
        )
    return broker_id


def _existing_transactions(cursor, portfolio_id: int, broker_id: int):
    cursor.execute(
        """
        SELECT ticker, transaction_type, transaction_date, quantity, unit_price,
               gross_amount, fees, taxes, net_cash_amount, notes
        FROM public.portfolio_transactions
        WHERE portfolio_id = %s AND broker_id = %s AND source = %s
        """,
        (portfolio_id, broker_id, TRANSACTION_SOURCE),
    )
    existing = {}
    for record in cursor.fetchall():
        reference = _reference_from_notes(record[9])
        if reference is None:
            continue
        if reference in existing:
            raise ValueError(
                f"Riferimento di import duplicato già presente nel DB: {reference}."
            )
        existing[reference] = record
    return existing


def _database_record(transaction: Transaction) -> tuple[object, ...]:
    return (
        transaction.ticker,
        transaction.transaction_type,
        transaction.transaction_date,
        transaction.quantity,
        transaction.unit_price,
        transaction.gross_amount,
        transaction.fees,
        MONEY_ZERO,
        transaction.net_cash_amount,
        transaction.notes,
    )


def import_transactions(
    transactions: list[Transaction],
    portfolio_id: int,
    database_url: str,
) -> tuple[int, int]:
    try:
        db_driver = importlib.import_module("psycopg2")
    except ModuleNotFoundError as error:
        if error.name != "psycopg2":
            raise
        raise RuntimeError(
            "La libreria psycopg2 non è installata. "
            "Esegui 'python -m pip install -r requirements.txt'."
        ) from error

    connection = db_driver.connect(database_url)
    try:
        with connection:
            with connection.cursor() as cursor:
                broker_id = _validate_database_targets(
                    cursor, portfolio_id, transactions
                )
                existing = _existing_transactions(cursor, portfolio_id, broker_id)
                pending = []
                already_imported = 0
                for transaction in transactions:
                    old_record = existing.get(transaction.import_reference)
                    if old_record is None:
                        pending.append(
                            _transaction_values(transaction, portfolio_id, broker_id)
                        )
                    elif tuple(old_record) != _database_record(transaction):
                        raise ValueError(
                            "Il riferimento di import esiste già con dati diversi "
                            f"nel DB: {transaction.import_reference}."
                        )
                    else:
                        already_imported += 1

                if pending:
                    cursor.executemany(
                        """
                        INSERT INTO public.portfolio_transactions (
                            portfolio_id, broker_id, ticker, transaction_type,
                            transaction_date, quantity, unit_price, gross_amount,
                            fees, taxes, net_cash_amount, source, notes
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        pending,
                    )
    finally:
        connection.close()
    return len(pending), already_imported


def _load_database_url() -> str:
    load_dotenv(ROOT / ".env")
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError(
            "DATABASE_URL non è impostata nell'ambiente o nel file .env."
        )
    return database_url


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Importa i movimenti del CSV Degiro in portfolio_transactions."
    )
    parser.add_argument(
        "--statement",
        type=Path,
        default=ROOT / "import" / "Degiro Account.csv",
        help="CSV dei movimenti (predefinito: import/Degiro Account.csv).",
    )
    parser.add_argument(
        "--anagrafica",
        type=Path,
        default=ROOT / "Anagrafica.csv",
        help="CSV ISIN/ticker (predefinito: Anagrafica.csv).",
    )
    parser.add_argument(
        "--portfolio-id",
        type=int,
        required=True,
        help="ID del portafoglio di destinazione.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Scrive nel database; senza questa opzione mostra solo il riepilogo.",
    )
    args = parser.parse_args()

    try:
        transactions, skipped_rows, unsupported_rows = build_import_plan(
            args.statement, args.anagrafica
        )
        if unsupported_rows:
            print(
                f"ATTENZIONE: {len(unsupported_rows)} righe non riconosciute; "
                "verranno ignorate."
            )
            for row in unsupported_rows:
                date_label = (
                    row.transaction_date.isoformat()
                    if row.transaction_date is not None
                    else "data mancante"
                )
                amount_label = (
                    f"{row.amount} EUR" if row.amount is not None else "importo vuoto"
                )
                print(
                    f"  Riga {row.number} ({date_label}, {amount_label}): "
                    f"{row.description}"
                )
        else:
            print("Nessuna riga con tipologia non riconosciuta.")
        counts = Counter(transaction.transaction_type for transaction in transactions)
        print(
            f"Movimenti da importare: {len(transactions)} "
            f"({', '.join(f'{kind}={count}' for kind, count in sorted(counts.items()))})"
        )
        print(
            f"Righe ignorate (cash sweep, variazione zero/vuota "
            f"o tipologia non riconosciuta): {skipped_rows}"
        )
        if not args.apply:
            print("Anteprima: nessuna scrittura eseguita. Aggiungi --apply per importare.")
            return 0
        inserted, already_imported = import_transactions(
            transactions,
            args.portfolio_id,
            _load_database_url(),
        )
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Errore: {error}")
        return 1

    print(
        f"Import completato: {inserted} inseriti, "
        f"{already_imported} già presenti."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
