import importlib
import math
import os
from datetime import date, datetime, timedelta
from pathlib import Path

import yfinance as yf
from dotenv import load_dotenv

from eodhd import get_eodhd_history
from instruments_db import load_instruments
from simpletools_prices import SimpleToolsClient

PRICE_HISTORY_PERIOD = "1mo"
DIVIDEND_HISTORY_START_DATE = "2022-01-01"
SIMPLETOOLS_CLIENT = SimpleToolsClient()

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


def get_etf_prices(yahoo_ticker):
    print(f"Recupero dati per {yahoo_ticker}...")
    etf = yf.Ticker(yahoo_ticker)

    df = etf.history(period=PRICE_HISTORY_PERIOD, auto_adjust=False)
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


def get_etf_dividends(yahoo_ticker):
    print(f"Recupero dividendi per {yahoo_ticker}...")
    dividends = yf.Ticker(yahoo_ticker).dividends
    if dividends.empty:
        print(f"Nessun dividendo storico disponibile per {yahoo_ticker}.")
        return []

    rows = []
    for event_date, amount in dividends.items():
        amount = float(amount)
        if not math.isfinite(amount) or amount <= 0:
            raise ValueError(
                f"Importo del dividendo non valido per {yahoo_ticker} "
                f"alla data {event_date}"
            )
        rows.append((event_date.strftime("%Y-%m-%d"), amount))
    return rows


def get_eodhd_prices(eodhd_ticker):
    print(f"Recupero dati per {eodhd_ticker} da EODHD...")
    end_date = date.today()
    start_date = end_date - timedelta(days=30)
    prices = get_eodhd_history(
        eodhd_ticker, start_date.isoformat(), end_date.isoformat()
    )
    if not prices:
        print(
            f"Nessuna quotazione disponibile negli ultimi "
            f"{PRICE_HISTORY_PERIOD} per {eodhd_ticker}; salto."
        )
    return prices


def get_simpletools_prices(stfi_isin):
    print(f"Recupero dati per ISIN {stfi_isin} da SimpleTools...")
    end_date = date.today()
    start_date = end_date - timedelta(days=30)
    prices = SIMPLETOOLS_CLIENT.fetch_history(stfi_isin)
    recent_prices = [
        (date_str, price)
        for date_str, price in prices
        if start_date.isoformat() <= date_str <= end_date.isoformat()
    ]
    if not recent_prices:
        print(
            f"Nessuna quotazione disponibile negli ultimi "
            f"{PRICE_HISTORY_PERIOD} per ISIN {stfi_isin}; salto."
        )
    return recent_prices


def get_simpletools_dividends(stfi_isin):
    print(f"Recupero cedole SimpleTools per ISIN {stfi_isin}...")
    dividends = SIMPLETOOLS_CLIENT.fetch_dividends(
        stfi_isin, DIVIDEND_HISTORY_START_DATE
    )
    if not dividends:
        print(f"Nessuna cedola SimpleTools disponibile per ISIN {stfi_isin}.")
    return dividends


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


def save_dividends_to_db(dividends, ticker, db_driver):
    if not dividends:
        print(f"Nessun dividendo da salvare per {ticker}.")
        return

    connection = db_driver.connect(DATABASE_URL)
    try:
        with connection:
            with connection.cursor() as cursor:
                cursor.executemany(
                    """
                    INSERT INTO dividends (date, ticker, amount)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (date, ticker)
                    DO UPDATE SET amount = EXCLUDED.amount;
                    """,
                    [
                        (event_date, ticker, amount)
                        for event_date, amount in dividends
                    ],
                )
    finally:
        connection.close()

    print(f"Salvati {len(dividends)} dividendi per {ticker}.")


def process_instrument(
    ticker, yahoo_ticker, eodhd_ticker, stfi_isin, tipo, db_driver
):
    source_ticker = yahoo_ticker or eodhd_ticker or stfi_isin
    try:
        if yahoo_ticker:
            prices = get_etf_prices(yahoo_ticker)
        elif eodhd_ticker:
            prices = get_eodhd_prices(eodhd_ticker)
        else:
            prices = get_simpletools_prices(stfi_isin)
    except Exception as error:
        print(
            f"Errore durante il recupero delle quotazioni per "
            f"{source_ticker} ({ticker}): {error}. "
            "Continuo con le altre elaborazioni e con i ticker successivi."
        )
    else:
        if prices is not None:
            save_to_db(prices, ticker, db_driver)

    if yahoo_ticker:
        try:
            dividends = get_etf_dividends(yahoo_ticker)
        except Exception as error:
            print(
                f"Errore durante il recupero dei dividendi per "
                f"{yahoo_ticker} ({ticker}): {error}. "
                "Continuo con i ticker successivi."
            )
        else:
            save_dividends_to_db(dividends, ticker, db_driver)
    elif not eodhd_ticker and tipo and tipo.strip().lower() == "bond":
        try:
            dividends = get_simpletools_dividends(stfi_isin)
        except Exception as error:
            print(
                f"Errore durante il recupero delle cedole SimpleTools per "
                f"{stfi_isin} ({ticker}): {error}. "
                "Continuo con i ticker successivi."
            )
        else:
            save_dividends_to_db(dividends, ticker, db_driver)
    else:
        print(
            f"Nessun recupero dividendi configurato per {ticker} "
            "(sorgenti senza supporto dividendi)."
        )


if __name__ == "__main__":
    try:
        db_driver = load_psycopg2()
        if not DATABASE_URL:
            raise ValueError(
                "La variabile d'ambiente DATABASE_URL non è impostata."
            )

        instruments = load_instruments(DATABASE_URL, db_driver)

        for ticker, yahoo_ticker, eodhd_ticker, stfi_isin, _, tipo in instruments:
            process_instrument(
                ticker,
                yahoo_ticker,
                eodhd_ticker,
                stfi_isin,
                tipo,
                db_driver,
            )
    except Exception as e:
        print(f"Errore durante l'esecuzione: {e}")
        raise SystemExit(1) from e
