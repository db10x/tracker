import csv
import importlib
import os
from datetime import date, datetime
from pathlib import Path

import yfinance as yf
from dotenv import load_dotenv

PRICE_HISTORY_PERIOD = "1mo"

# Carica la configurazione locale senza sovrascrivere le variabili già impostate.
load_dotenv(Path(__file__).with_name(".env"))
DATABASE_URL = os.environ.get("DATABASE_URL")


def load_psycopg2():
    try:
        return importlib.import_module("psycopg2")
    except ModuleNotFoundError as error:
        if error.name != "psycopg2":
            raise
        raise RuntimeError(
            "La libreria psycopg2 non è installata. "
            "Esegui 'python -m pip install -r requirements.txt'."
        ) from error


def load_instruments():
    anagrafica_path = Path(__file__).with_name("Anagrafica.csv")
    with anagrafica_path.open(encoding="utf-8-sig", newline="") as csv_file:
        reader = csv.DictReader(csv_file, delimiter=";")
        required_columns = {"Ticker", "Ticker_yf", "Descrizione"}
        if reader.fieldnames is None or not required_columns.issubset(reader.fieldnames):
            missing_columns = required_columns - set(reader.fieldnames or [])
            raise ValueError(
                "Colonne mancanti in Anagrafica.csv: "
                + ", ".join(sorted(missing_columns))
            )

        instruments = []
        seen_tickers = set()
        for row_number, row in enumerate(reader, start=2):
            ticker = (row.get("Ticker") or "").strip()
            yahoo_ticker = (row.get("Ticker_yf") or "").strip()
            if not ticker:
                raise ValueError(
                    f"Ticker vuoto in Anagrafica.csv alla riga {row_number}"
                )
            if not yahoo_ticker:
                raise ValueError(
                    f"Ticker_yf vuoto in Anagrafica.csv alla riga {row_number}"
                )
            if ticker in seen_tickers:
                raise ValueError(
                    f"Ticker duplicato '{ticker}' in Anagrafica.csv "
                    f"alla riga {row_number}"
                )
            seen_tickers.add(ticker)
            description = (row.get("Descrizione") or "").strip() or None
            instruments.append((ticker, yahoo_ticker, description))

    if not instruments:
        raise ValueError("Anagrafica.csv non contiene ticker da elaborare")
    return instruments


def upsert_instruments(cursor, instruments):
    query = """
        INSERT INTO instruments (ticker, description)
        VALUES (%s, %s)
        ON CONFLICT (ticker)
        DO UPDATE SET description = EXCLUDED.description;
    """
    cursor.executemany(
        query,
        [(ticker, description) for ticker, _, description in instruments],
    )


def sync_instruments_to_db(instruments, db_driver):
    connection = db_driver.connect(DATABASE_URL)
    try:
        with connection:
            with connection.cursor() as cursor:
                upsert_instruments(cursor, instruments)
    finally:
        connection.close()


def get_etf_prices(yahoo_ticker):
    print(f"Recupero dati per {yahoo_ticker}...")
    etf = yf.Ticker(yahoo_ticker)

    df = etf.history(period=PRICE_HISTORY_PERIOD)
    if df.empty:
        print(
            f"Nessuna quotazione disponibile negli ultimi "
            f"{PRICE_HISTORY_PERIOD} per {yahoo_ticker}; salto."
        )
        return None

    if "Close" not in df.columns:
        raise ValueError(f"La colonna 'Close' manca per {yahoo_ticker}")

    close_prices = df["Close"].dropna()
    if close_prices.empty:
        print(
            f"Nessun prezzo di chiusura disponibile negli ultimi "
            f"{PRICE_HISTORY_PERIOD} per {yahoo_ticker}; salto."
        )
        return None

    return [
        (session_date.strftime("%Y-%m-%d"), float(close_price))
        for session_date, close_price in close_prices.items()
    ]


def select_prices_to_save(prices, existing_dates, today_date):
    return [
        (date_str, price)
        for date_str, price in prices
        if date_str not in existing_dates or date_str == today_date
    ]


def save_to_db(prices, ticker, db_driver):
    if not prices:
        return

    print("Connessione al database...")
    connection = db_driver.connect(DATABASE_URL)
    try:
        with connection:
            with connection.cursor() as cursor:
                date_strings = [date_str for date_str, _ in prices]
                start_date = date.fromisoformat(min(date_strings))
                end_date = date.fromisoformat(max(date_strings))
                cursor.execute(
                    """
                    SELECT date
                    FROM prices
                    WHERE ticker = %s AND date BETWEEN %s AND %s;
                    """,
                    (ticker, start_date, end_date),
                )
                existing_dates = {
                    existing_date.isoformat()
                    for (existing_date,) in cursor.fetchall()
                }
                today_date = datetime.now().date().isoformat()
                prices_to_save = select_prices_to_save(
                    prices, existing_dates, today_date
                )
                if prices_to_save:
                    cursor.executemany(
                        """
                        INSERT INTO prices (date, ticker, price)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (date, ticker)
                        DO UPDATE SET price = EXCLUDED.price;
                        """,
                        [
                            (date_str, ticker, price)
                            for date_str, price in prices_to_save
                        ],
                    )
    finally:
        connection.close()

    if not prices_to_save:
        print(f"Nessuna nuova quotazione da salvare per {ticker}.")
        return

    for date_str, price in prices_to_save:
        print(f"Salvato con successo: {date_str} - {ticker} -> {price} EUR")


if __name__ == "__main__":
    try:
        db_driver = load_psycopg2()
        if not DATABASE_URL:
            raise ValueError(
                "La variabile d'ambiente DATABASE_URL non è impostata."
            )

        instruments = load_instruments()
        sync_instruments_to_db(instruments, db_driver)

        for ticker, yahoo_ticker, _ in instruments:
            prices = get_etf_prices(yahoo_ticker)
            if prices is None:
                continue
            save_to_db(prices, ticker, db_driver)
    except Exception as e:
        print(f"Errore durante l'esecuzione: {e}")
        raise SystemExit(1) from e
