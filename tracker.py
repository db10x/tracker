import os
from datetime import datetime
import psycopg2
import yfinance as yf

# Configurazione
TICKER = "SWDA.MI"
# Legge la stringa di connessione dalle variabili d'ambiente di GitHub
DATABASE_URL = os.environ.get("DATABASE_URL")


def get_etf_price():
  print(f"Recupero dati per {TICKER}...")
  etf = yf.Ticker(TICKER)
  # Prende i dati dell'ultimo giorno disponibile
  df = etf.history(period="1d")
  if df.empty:
    raise ValueError(f"Nessun dato trovato per {TICKER}")

  close_price = float(df["Close"].iloc[0])
  today_date = datetime.now().strftime("%Y-%m-%d")
  return today_date, close_price


def save_to_db(date_str, price):
  print(f"Connessione al database...")
  conn = psycopg2.connect(DATABASE_URL)
  cursor = conn.cursor()

  # Inserisce il record. Se esiste già per la stessa data, lo aggiorna (UPSERT)
  query = """
        INSERT INTO etf_prices (date, ticker, price)
        VALUES (%s, %s, %s)
        ON CONFLICT (date) 
        DO UPDATE SET price = EXCLUDED.price;
    """
  cursor.execute(query, (date_str, TICKER, price))

  conn.commit()
  cursor.close()
  conn.close()
  print(f"Salvato con successo: {date_str} -> {price} EUR")


if __name__ == "__main__":
  try:
    d, p = get_etf_price()
    save_to_db(d, p)
  except Exception as e:
    print(f"Errore durante l'esecuzione: {e}")
    exit(1)
