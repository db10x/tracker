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
    yahoo_instrument = yf.Ticker(yahoo_ticker)
    history = yahoo_instrument.history(
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

    dividend_rows = []
    for event_date, amount in yahoo_instrument.dividends.items():
        event_date_str = event_date.strftime("%Y-%m-%d")
        if event_date_str < HISTORY_START_DATE:
            continue

        amount = float(amount)
        if not math.isfinite(amount) or amount <= 0:
            raise ValueError(
                f"Importo del dividendo non valido per {yahoo_ticker} "
                f"alla data {event_date_str}"
            )
        dividend_rows.append((event_date_str, ticker, amount))

    print(
        f"{ticker}: {len(rows)} quotazioni "
        f"dal {rows[0][0]} al {rows[-1][0]}; "
        f"{len(dividend_rows)} dividendi storici"
    )
    return rows, dividend_rows


def save_history_to_db(
    price_rows, dividend_rows, instruments, database_url, db_driver
):
    execute_values = importlib.import_module("psycopg2.extras").execute_values
    price_query = """
        INSERT INTO prices (date, ticker, price)
        VALUES %s
        ON CONFLICT (date, ticker)
        DO UPDATE SET price = EXCLUDED.price;
    """
    dividend_query = """
        INSERT INTO dividends (date, ticker, amount)
        VALUES %s
        ON CONFLICT (date, ticker)
        DO UPDATE SET amount = EXCLUDED.amount;
    """

    connection = db_driver.connect(database_url)
    try:
        with connection:
            with connection.cursor() as cursor:
                upsert_instruments(cursor, instruments)
                if price_rows:
                    execute_values(
                        cursor, price_query, price_rows, page_size=1000
                    )
                if dividend_rows:
                    execute_values(
                        cursor, dividend_query, dividend_rows, page_size=1000
                    )
    finally:
        connection.close()

    print(
        f"Salvate {len(price_rows)} quotazioni e "
        f"{len(dividend_rows)} dividendi storici nel database."
    )


def main():
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise ValueError("La variabile d'ambiente DATABASE_URL non è impostata.")

    instruments = load_instruments()
    price_rows = []
    dividend_rows = []
    for ticker, yahoo_ticker, _ in instruments:
        ticker_prices, ticker_dividends = get_ticker_history(ticker, yahoo_ticker)
        price_rows.extend(ticker_prices)
        dividend_rows.extend(ticker_dividends)

    db_driver = importlib.import_module("psycopg2")
    save_history_to_db(
        price_rows, dividend_rows, instruments, database_url, db_driver
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"Errore durante il caricamento dello storico: {error}")
        raise SystemExit(1) from error
