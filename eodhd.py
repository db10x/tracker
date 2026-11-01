import json
import math
import os
from datetime import date, timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import urlopen

EODHD_MAX_HISTORY_DAYS = 365


def get_eodhd_history(eodhd_ticker, start_date, end_date):
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    earliest_allowed_date = date.today() - timedelta(
        days=EODHD_MAX_HISTORY_DAYS
    )
    if start < earliest_allowed_date:
        raise ValueError(
            f"La data iniziale richiesta a EODHD ({start}) supera il limite "
            f"di {EODHD_MAX_HISTORY_DAYS} giorni del piano gratuito."
        )
    if end < start or end > date.today():
        raise ValueError(f"Intervallo date non valido per EODHD: {start} - {end}")

    api_token = os.environ.get("EODHD_API_TOKEN")
    if not api_token:
        raise ValueError(
            "La variabile d'ambiente EODHD_API_TOKEN non è impostata."
        )

    query = urlencode(
        {
            "api_token": api_token,
            "from": start.isoformat(),
            "to": end.isoformat(),
            "period": "d",
            "fmt": "json",
        }
    )
    url = (
        f"https://eodhd.com/api/eod/{quote(eodhd_ticker, safe='')}?{query}"
    )
    try:
        with urlopen(url, timeout=30) as response:
            data = json.load(response)
    except HTTPError as error:
        raise RuntimeError(
            f"Richiesta EODHD non riuscita per {eodhd_ticker} "
            f"(HTTP {error.code})."
        ) from error
    except URLError as error:
        raise RuntimeError(
            f"Connessione a EODHD non riuscita per {eodhd_ticker}: "
            f"{error.reason}"
        ) from error

    if not isinstance(data, list):
        message = data.get("message") if isinstance(data, dict) else None
        detail = f": {message}" if message else ""
        raise RuntimeError(
            f"Risposta EODHD non valida per {eodhd_ticker}{detail}"
        )

    prices = []
    for row in data:
        if not isinstance(row, dict) or "date" not in row:
            raise ValueError(
                f"Riga storica EODHD non valida per {eodhd_ticker}"
            )
        session_date = date.fromisoformat(row["date"])
        if session_date < start or session_date > end:
            continue

        close_price = row.get("close")
        if close_price is None:
            continue
        price = float(close_price)
        if not math.isfinite(price):
            raise ValueError(
                f"Prezzo di chiusura EODHD non valido per {eodhd_ticker} "
                f"alla data {session_date}"
            )
        prices.append((session_date.isoformat(), price))

    return sorted(prices)
