def load_instruments(database_url, db_driver):
    connection = db_driver.connect(database_url)
    try:
        with connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT ticker, ticker_yf, ticker_eodhd, isin_stfi, description, tipo
                    FROM public.instruments
                    ORDER BY ticker;
                    """
                )
                rows = cursor.fetchall()
    finally:
        connection.close()

    if not rows:
        raise ValueError(
            "La tabella public.instruments non contiene strumenti. "
            "Aggiungi l'anagrafica direttamente in Supabase."
        )

    instruments = []
    seen_tickers = set()
    for row_number, row in enumerate(rows, start=1):
        ticker, yahoo_ticker, eodhd_ticker, stfi_isin, description, tipo = row
        ticker = (ticker or "").strip()
        yahoo_ticker = (yahoo_ticker or "").strip()
        eodhd_ticker = (eodhd_ticker or "").strip()
        stfi_isin = (stfi_isin or "").strip()
        if not ticker:
            raise ValueError(
                f"Ticker vuoto in public.instruments alla riga {row_number}"
            )
        if not yahoo_ticker and not eodhd_ticker and not stfi_isin:
            raise ValueError(
                f"Ticker_yf, Ticker_EODHD e ISIN_stfi vuoti per "
                f"{ticker} in public.instruments"
            )
        if ticker in seen_tickers:
            raise ValueError(
                f"Ticker duplicato '{ticker}' in public.instruments"
            )
        seen_tickers.add(ticker)
        instruments.append(
            (
                ticker,
                yahoo_ticker,
                eodhd_ticker,
                stfi_isin,
                (description or "").strip() or None,
                (tipo or "").strip() or None,
            )
        )

    return instruments
