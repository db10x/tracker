from __future__ import annotations

import json
import math
import re
import time
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urljoin, urlsplit
from urllib.request import Request, urlopen


BASE_URL = "https://www.simpletoolsforinvestors.eu/"
MONITOR_URLS = (
    urljoin(BASE_URL, "monitor_info.php?monitor=5&yieldtype=G&timescale=DUR"),
    urljoin(BASE_URL, "monitor_info.php?monitor=66&yieldtype=G&timescale=DUR"),
)
JSON_PATTERN = re.compile(r"\bconst\s+json\s*=\s*(\{.*?\})\s*;", re.DOTALL)
XBD_PATTERN = re.compile(r"\bvar\s+XBD\s*=\s*", re.DOTALL)


class _MonitorParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.history_urls: dict[str, str] = {}
        self._in_row = False
        self._in_cell = False
        self._cell_text: list[str] = []
        self._cells: list[str] = []
        self._history_href: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "tr":
            self._in_row = True
            self._cells = []
            self._history_href = None
        elif self._in_row and tag == "td":
            self._in_cell = True
            self._cell_text = []
        elif self._in_row and tag == "a":
            href = attributes.get("href", "") or ""
            if "historicalgraph.php" in href:
                self._history_href = href

    def handle_data(self, data: str) -> None:
        if self._in_row and self._in_cell:
            self._cell_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "td" and self._in_row and self._in_cell:
            self._cells.append("".join(self._cell_text).strip())
            self._in_cell = False
        elif tag == "tr" and self._in_row:
            self._store_row()
            self._in_row = False

    def _store_row(self) -> None:
        if not self._cells or not self._history_href:
            return

        isin = self._cells[0].strip().upper()
        query = parse_qs(urlsplit(self._history_href).query)
        bond_id = query.get("bondid", [""])[0]
        market_code = query.get("marketcode", [""])[0]
        if isin and bond_id and market_code:
            self.history_urls[isin] = urljoin(
                BASE_URL,
                f"historicalgraph.php?bondid={bond_id}&marketcode={market_code}",
            )


class SimpleToolsClient:
    def __init__(self, timeout: int = 30, pause: float = 0.2) -> None:
        self.timeout = timeout
        self.pause = pause
        self._history_urls: dict[str, str] | None = None

    def _get_html(self, url: str) -> str:
        request = Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) SimpleToolsPriceImporter/1.0",
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "it-IT,it;q=0.9,en;q=0.8",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return response.read().decode("utf-8", errors="replace")
        except HTTPError as exc:
            raise RuntimeError(f"HTTP {exc.code} durante il GET {url}") from exc
        except URLError as exc:
            raise RuntimeError(f"Errore di rete durante il GET {url}: {exc.reason}") from exc

    def _load_history_urls(self) -> dict[str, str]:
        if self._history_urls is not None:
            return self._history_urls

        history_urls: dict[str, str] = {}
        errors: list[str] = []
        for monitor_url in MONITOR_URLS:
            try:
                parser = _MonitorParser()
                parser.feed(self._get_html(monitor_url))
                history_urls.update(parser.history_urls)
            except Exception as exc:
                errors.append(str(exc))
            time.sleep(self.pause)

        if not history_urls:
            details = "; ".join(errors) if errors else "nessun link storico trovato"
            raise RuntimeError(f"Impossibile leggere i monitor SimpleTools: {details}")

        self._history_urls = history_urls
        return history_urls

    @staticmethod
    def _market_date(timestamp_ms: int) -> str:
        utc_datetime = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)
        if utc_datetime.hour not in (22, 23) or utc_datetime.minute != 0:
            raise ValueError(
                f"Timestamp del grafico inatteso: {utc_datetime.isoformat()} UTC"
            )
        return (utc_datetime.date() + timedelta(days=1)).isoformat()

    def fetch_history(self, provider_isin: str) -> list[tuple[str, float]]:
        isin = provider_isin.strip().upper()
        if not isin:
            raise ValueError("ISIN SimpleTools non configurato nell'anagrafica")

        history_url = self._load_history_urls().get(isin)
        if history_url is None:
            raise LookupError(f"ISIN {isin} non trovato nei monitor SimpleTools supportati")

        html = self._get_html(history_url)
        match = JSON_PATTERN.search(html)
        if match is None:
            raise ValueError(f"Dati JSON del grafico non trovati per {isin}")

        payload = json.loads(match.group(1))
        returned_isin = str(payload.get("isincode", "")).strip().upper()
        if returned_isin != isin:
            raise ValueError(
                f"ISIN non corrispondente: richiesto {isin}, ricevuto {returned_isin or 'vuoto'}"
            )

        points: list[tuple[str, float]] = []
        for item in payload.get("data", []):
            if len(item) < 2 or item[0] is None or item[1] is None:
                continue
            market_date = self._market_date(int(item[0]))
            market_price = float(item[1])
            points.append((market_date, market_price))

        if not points:
            raise ValueError(f"Nessun prezzo storico restituito per {isin}")

        time.sleep(self.pause)
        return points

    @staticmethod
    def _extract_bond_data(html: str) -> dict:
        match = XBD_PATTERN.search(html)
        if match is None:
            raise ValueError("Dati dell'obbligazione non trovati nel calcolatore SimpleTools")

        start = html.find("{", match.end())
        if start < 0:
            raise ValueError("Oggetto dell'obbligazione non valido nel calcolatore SimpleTools")

        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(html)):
            char = html[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
            elif char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return json.loads(html[start:index + 1])

        raise ValueError("Oggetto dell'obbligazione incompleto nel calcolatore SimpleTools")

    @staticmethod
    def _add_months_js(value: date, months: int) -> date:
        month_index = value.year * 12 + value.month - 1 + months
        year, month = divmod(month_index, 12)
        return date(year, month + 1, 1) + timedelta(days=value.day - 1)

    @staticmethod
    def _round_simpletools_amount(amount: float) -> float:
        if not math.isfinite(amount):
            raise ValueError("Importo cedola non valido nel piano SimpleTools")
        return math.floor(amount * 100 + 0.5) / 100

    def _standard_coupon_rows(
        self, bond: dict, start_date: date, end_date: date
    ) -> list[tuple[str, float]]:
        periodicity = bond.get("couponperiodicity")
        months_per_coupon = {1: 12, 2: 6, 4: 3}.get(periodicity)
        coupon_rate = bond.get("couponrate")
        if months_per_coupon is None or not isinstance(coupon_rate, (int, float)):
            raise ValueError("Piano cedolare standard SimpleTools non supportato")
        if not math.isfinite(coupon_rate) or coupon_rate <= 0:
            raise ValueError("Tasso cedolare SimpleTools non valido")

        issue_date = date.fromisoformat(bond["issuedate"])
        redemption_date = date.fromisoformat(bond["redemptiondate"])
        first_relevant_date = max(start_date, issue_date + timedelta(days=1))
        if first_relevant_date > end_date or first_relevant_date > redemption_date:
            return []

        settlement_date = first_relevant_date - timedelta(days=1)
        previous_date = redemption_date
        while previous_date > settlement_date:
            previous_date = self._add_months_js(previous_date, -months_per_coupon)
        first_coupon_date = self._add_months_js(previous_date, months_per_coupon)

        nominal_10000_amount = self._round_simpletools_amount(
            10000 * coupon_rate / periodicity
        )
        amount_per_100_nominal = nominal_10000_amount / 100
        rows = []
        index = 0
        while True:
            coupon_date = self._add_months_js(
                first_coupon_date, months_per_coupon * index
            )
            if coupon_date > redemption_date or coupon_date > end_date:
                break
            if coupon_date >= start_date:
                rows.append((coupon_date.isoformat(), amount_per_100_nominal))
            index += 1

        return rows

    def _custom_coupon_rows(
        self, bond: dict, start_date: date, end_date: date
    ) -> list[tuple[str, float]]:
        issue_date = date.fromisoformat(bond["issuedate"])
        rows = []
        for coupon in bond["couponplan"]:
            coupon_date = date.fromisoformat(coupon["coupondate"][:10])
            if not max(start_date, issue_date + timedelta(days=1)) <= coupon_date <= end_date:
                continue
            nominal_amount = self._round_simpletools_amount(
                10000 * float(coupon["couponvalue"])
            )
            if nominal_amount <= 0:
                raise ValueError(
                    f"Importo cedola SimpleTools non valido per {coupon_date}"
                )
            rows.append((coupon_date.isoformat(), nominal_amount / 100))
        return rows

    def fetch_dividends(
        self, provider_isin: str, start_date: str | date
    ) -> list[tuple[str, float]]:
        isin = provider_isin.strip().upper()
        if not isin:
            raise ValueError("ISIN SimpleTools non configurato nell'anagrafica")

        first_date = (
            date.fromisoformat(start_date)
            if isinstance(start_date, str)
            else start_date
        )
        last_date = date.today()
        if first_date > last_date:
            return []

        history_url = self._load_history_urls().get(isin)
        if history_url is None:
            raise LookupError(f"ISIN {isin} non trovato nei monitor SimpleTools supportati")
        query = parse_qs(urlsplit(history_url).query)
        bond_id = query.get("bondid", [""])[0]
        if not bond_id:
            raise ValueError(f"ID obbligazione non trovato per ISIN {isin}")

        calculator_url = urljoin(
            BASE_URL, f"compute.php?bondID={bond_id}&price=100"
        )
        bond = self._extract_bond_data(self._get_html(calculator_url))
        returned_isin = str(bond.get("isincode", "")).strip().upper()
        if returned_isin != isin:
            raise ValueError(
                f"ISIN non corrispondente: richiesto {isin}, "
                f"ricevuto {returned_isin or 'vuoto'}"
            )

        bond_type = bond.get("bondtype")
        if bond_type == "PV":
            dividends = self._standard_coupon_rows(bond, first_date, last_date)
        elif bond_type == "CC" and isinstance(bond.get("couponplan"), list):
            dividends = self._custom_coupon_rows(bond, first_date, last_date)
        else:
            dividends = []

        time.sleep(self.pause)
        return dividends