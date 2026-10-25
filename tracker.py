import csv
import importlib
import os
from datetime import datetime
from pathlib import Path

import yfinance as yf

# Legge la stringa di connessione dalle variabili d'ambiente
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


def load_tickers():
    anagrafica_path = Path(__file__).with_name("Anagrafica.csv")
    with anagrafica_path.open(encoding="utf-8-sig", newline="") as csv_file:
        reader = csv.DictReader(csv_file, delimiter=";")
        if reader.fieldnames is None or "Ticker_yf" not in reader.fieldnames:
            raise ValueError("La colonna 'Ticker_yf' manca in Anagrafica.csv")

        tickers = []
        for row_number, row in enumerate(reader, start=2):
            ticker = (row.get("Ticker_yf") or "").strip()
            if not ticker:
                raise ValueError(
                    f"Ticker_yf vuoto in Anagrafica.csv alla riga {row_number}"
                )
            tickers.append(ticker)

    if not tickers:
        raise ValueError("Anagrafica.csv non contiene ticker da elaborare")
    return tickers


def get_etf_price(ticker):
    print(f"Recupero dati per {ticker}...")
    etf = yf.Ticker(ticker)

    # Include l'ultima chiusura disponibile anche nei giorni senza contrattazioni.
    df = etf.history(period="5d")
    if df.empty:
        raise ValueError(f"Nessun dato trovato per {ticker}")

    close_price = float(df["Close"].iloc[-1])
    today_date = datetime.now().strftime("%Y-%m-%d")

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

        for ticker in load_tickers():
            date_str, price = get_etf_price(ticker)
            save_to_db(date_str, ticker, price, db_driver)
    except Exception as e:
        print(f"Errore durante l'esecuzione: {e}")
        raise SystemExit(1) from e
