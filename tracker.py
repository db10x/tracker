import csv
import importlib
import os
from datetime import datetime
from pathlib import Path

import yfinance as yf
from dotenv import load_dotenv

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


def get_etf_price(yahoo_ticker):
    print(f"Recupero dati per {yahoo_ticker}...")
    etf = yf.Ticker(yahoo_ticker)

    df = etf.history(period="1d")
    if df.empty:
        print(f"Nessuna quotazione disponibile oggi per {yahoo_ticker}; salto.")
        return None

    today_date = datetime.now().strftime("%Y-%m-%d")
    today_data = df.loc[df.index.strftime("%Y-%m-%d") == today_date]
    if today_data.empty:
        print(f"Nessuna quotazione disponibile oggi per {yahoo_ticker}; salto.")
        return None

    today_closes = today_data["Close"].dropna()
    if today_closes.empty:
        print(
            f"Nessun prezzo di chiusura disponibile oggi per {yahoo_ticker}; salto."
        )
        return None

    close_price = float(today_closes.iloc[-1])

    return today_date, close_price


def save_to_db(date_str, ticker, price, db_driver):
    print("Connessione al database...")
    conn = db_driver.connect(DATABASE_URL)
    cursor = conn.cursor()

    query = """
        INSERT INTO prices (date, ticker, price)
        VALUES (%s, %s, %s)
        ON CONFLICT (date, ticker)
        DO UPDATE SET price = EXCLUDED.price;
    """
    cursor.execute(query, (date_str, ticker, price))

    conn.commit()
    cursor.close()
    conn.close()
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
            result = get_etf_price(yahoo_ticker)
            if result is None:
                continue
            date_str, price = result
            save_to_db(date_str, ticker, price, db_driver)
    except Exception as e:
        print(f"Errore durante l'esecuzione: {e}")
        raise SystemExit(1) from e
