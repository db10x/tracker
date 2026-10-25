import importlib
import math
import os
from pathlib import Path

import yfinance as yf
from dotenv import load_dotenv

from tracker import load_instruments, upsert_instruments

load_dotenv(Path(__file__).with_name(".env"))

HISTORY_START_DATE = "2022-01-01"


def get_ticker_history(ticker, yahoo_ticker):
    print(f"Recupero storico per {ticker} ({yahoo_ticker})...")
    history = yf.Ticker(yahoo_ticker).history(
        start=HISTORY_START_DATE,
        auto_adjust=False,
    )
    if history.empty:
        raise ValueError(f"Nessun dato storico trovato per {yahoo_ticker}")
    if "Close" not in history.columns:
        raise ValueError(
            f"La colonna 'Close' manca nello storico di {yahoo_ticker}"
        )

    close_prices = history["Close"].dropna()
    if close_prices.empty:
        raise ValueError(f"Nessun prezzo di chiusura valido per {yahoo_ticker}")

    rows = []
    for session_date, close_price in close_prices.items():
        date_str = session_date.strftime("%Y-%m-%d")
        if date_str < HISTORY_START_DATE:
            continue

        price = float(close_price)
        if not math.isfinite(price):
            raise ValueError(
                f"Prezzo non valido nello storico di {yahoo_ticker}"
            )
        rows.append((date_str, ticker, price))

    if not rows:
        raise ValueError(
            f"Nessun prezzo di chiusura disponibile per {ticker} "
            f"({yahoo_ticker}) "
            f"dal {HISTORY_START_DATE}"
        )

    print(
        f"{ticker}: {len(rows)} quotazioni "
        f"dal {rows[0][0]} al {rows[-1][0]}"
    )
    return rows


def save_history_to_db(rows, instruments, database_url, db_driver):
    execute_values = importlib.import_module("psycopg2.extras").execute_values
    query = """
        INSERT INTO prices (date, ticker, price)
        VALUES %s
        ON CONFLICT (date, ticker)
        DO UPDATE SET price = EXCLUDED.price;
    """

    connection = db_driver.connect(database_url)
    try:
        with connection:
            with connection.cursor() as cursor:
                upsert_instruments(cursor, instruments)
                execute_values(cursor, query, rows, page_size=1000)
    finally:
        connection.close()

    print(f"Salvate {len(rows)} quotazioni storiche nel database.")


def main():
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise ValueError("La variabile d'ambiente DATABASE_URL non è impostata.")

    instruments = load_instruments()
    rows = []
    for ticker, yahoo_ticker, _ in instruments:
        rows.extend(get_ticker_history(ticker, yahoo_ticker))

    db_driver = importlib.import_module("psycopg2")
    save_history_to_db(rows, instruments, database_url, db_driver)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"Errore durante il caricamento dello storico: {error}")
        raise SystemExit(1) from error
