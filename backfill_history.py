import importlib
import math
import os
from datetime import date, timedelta
from pathlib import Path

import yfinance as yf
from dotenv import load_dotenv

from eodhd import EODHD_MAX_HISTORY_DAYS, get_eodhd_history
from instruments_db import load_instruments
from simpletools_prices import SimpleToolsClient

load_dotenv(Path(__file__).with_name(".env"))

HISTORY_START_DATE = "2022-01-01"
SIMPLETOOLS_CLIENT = SimpleToolsClient()


def get_ticker_history(ticker, yahoo_ticker, eodhd_ticker, stfi_isin, tipo):
    source_ticker = yahoo_ticker or eodhd_ticker or stfi_isin
    print(f"Recupero storico per {ticker} ({source_ticker})...")
    rows = []
    dividend_rows = []
    if yahoo_ticker:
        yahoo_instrument = yf.Ticker(yahoo_ticker)
        history = yahoo_instrument.history(
            start=HISTORY_START_DATE,
            auto_adjust=False,
        )
        if history.empty:
            print(
                f"Avviso: nessun dato storico dei prezzi trovato per "
                f"{yahoo_ticker}; continuo con dividendi e ticker successivi."
            )
        elif "Close" not in history.columns:
            print(
                f"Avviso: la colonna 'Close' manca nello storico di "
                f"{yahoo_ticker}; continuo con dividendi e ticker successivi."
            )
        else:
            close_prices = history["Close"].dropna()
            if close_prices.empty:
                print(
                    f"Avviso: nessun prezzo di chiusura valido trovato per "
                    f"{yahoo_ticker}; continuo con dividendi e ticker successivi."
                )
            else:
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
                print(
                    f"Avviso: nessun prezzo di chiusura disponibile per "
                    f"{ticker} ({yahoo_ticker}) dal {HISTORY_START_DATE}; "
                    "continuo con dividendi e ticker successivi."
                )

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
    elif eodhd_ticker:
        today = date.today()
        start_date = max(
            date.fromisoformat(HISTORY_START_DATE),
            today - timedelta(days=EODHD_MAX_HISTORY_DAYS),
        )
        if start_date > date.fromisoformat(HISTORY_START_DATE):
            print(
                f"EODHD: storico limitato agli ultimi "
                f"{EODHD_MAX_HISTORY_DAYS} giorni, dal {start_date}."
            )
        eodhd_prices = get_eodhd_history(
            eodhd_ticker, start_date.isoformat(), today.isoformat()
        )
        rows = [
            (date_str, ticker, price)
            for date_str, price in eodhd_prices
        ]
        if not rows:
            print(
                f"Avviso: nessun prezzo di chiusura disponibile per "
                f"{ticker} ({eodhd_ticker}); continuo con i ticker successivi."
            )
    else:
        simpletools_prices = SIMPLETOOLS_CLIENT.fetch_history(stfi_isin)
        rows = [
            (date_str, ticker, price)
            for date_str, price in simpletools_prices
            if date_str >= HISTORY_START_DATE
        ]
        if tipo and tipo.strip().lower() == "bond":
            simpletools_dividends = SIMPLETOOLS_CLIENT.fetch_dividends(
                stfi_isin, HISTORY_START_DATE
            )
            dividend_rows.extend(
                (event_date, ticker, amount)
                for event_date, amount in simpletools_dividends
            )
        if not rows:
            print(
                f"Avviso: nessun prezzo storico disponibile per "
                f"{ticker} (ISIN {stfi_isin}) dal {HISTORY_START_DATE}; "
                "continuo con i ticker successivi."
            )

    if rows:
        print(
            f"{ticker}: {len(rows)} quotazioni "
            f"dal {rows[0][0]} al {rows[-1][0]}; "
            f"{len(dividend_rows)} dividendi storici"
        )
    else:
        print(f"{ticker}: 0 quotazioni; {len(dividend_rows)} dividendi storici")
    return rows, dividend_rows


def save_history_to_db(
    price_rows, dividend_rows, database_url, db_driver
):
    execute_values = importlib.import_module("psycopg2.extras").execute_values
    price_query = """
        INSERT INTO prices (date, ticker, price)
        VALUES %s
        ON CONFLICT (date, ticker)
        DO NOTHING;
    """
    dividend_query = """
        INSERT INTO dividends (date, ticker, amount)
        VALUES %s
        ON CONFLICT (date, ticker)
        DO NOTHING;
    """

    connection = db_driver.connect(database_url)
    try:
        with connection:
            with connection.cursor() as cursor:
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
        f"Elaborate {len(price_rows)} quotazioni e "
        f"{len(dividend_rows)} righe di dividendi storici nel database."
    )


def main():
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise ValueError("La variabile d'ambiente DATABASE_URL non è impostata.")

    db_driver = importlib.import_module("psycopg2")
    instruments = load_instruments(database_url, db_driver)
    price_rows = []
    dividend_rows = []
    for ticker, yahoo_ticker, eodhd_ticker, stfi_isin, _, tipo in instruments:
        ticker_prices, ticker_dividends = get_ticker_history(
            ticker, yahoo_ticker, eodhd_ticker, stfi_isin, tipo
        )
        price_rows.extend(ticker_prices)
        dividend_rows.extend(ticker_dividends)

    save_history_to_db(
        price_rows, dividend_rows, database_url, db_driver
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"Errore durante il caricamento dello storico: {error}")
        raise SystemExit(1) from error
