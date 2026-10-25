# Contesto del progetto

Questa guida riassume lo stato del repository e le dipendenze tra codice e infrastruttura per facilitare le modifiche future. È stata ricavata dai file presenti nel repository, in particolare dal [README.md](./README.md), e non verifica lo stato effettivo delle impostazioni nei dashboard GitHub o Supabase.

## Scopo e flusso dei dati

Il progetto registra e visualizza lo storico dei prezzi dei titoli elencati in `Anagrafica.csv`, usando per ciascuno il ticker Yahoo Finance della colonna `Ticker_yf`.

1. Il workflow GitHub Actions `.github/workflows/tracker.yml` esegue `tracker.py` alle 18:00 UTC ogni giorno e può essere avviato anche manualmente.
2. `tracker.py` legge tutti i ticker dalla colonna `Ticker_yf` di `Anagrafica.csv` e recupera da Yahoo Finance tramite `yfinance` il prezzo di chiusura più recente per ognuno, cercando nello storico degli ultimi cinque giorni.
3. Lo script connette PostgreSQL su Supabase usando il secret Actions `DATABASE_URL` e inserisce o aggiorna il record del giorno.
4. `index.html` interroga Supabase direttamente dal browser usando la chiave pubblica, carica i record a pagine e mostra tabella e grafico Chart.js.

Non ci sono backend applicativi o framework di build: la UI è una pagina statica autonoma e lo script è un job batch.

## File e responsabilità

| File | Responsabilità |
| --- | --- |
| `tracker.py` | Recupero prezzo Yahoo Finance e upsert nel database PostgreSQL. |
| `index.html` | Pagina italiana con tabella storico, grafico, zoom e pan. |
| `requirements.txt` | Dipendenze Python (`yfinance`, `psycopg2-binary`). |
| `.github/workflows/tracker.yml` | Pianificazione e avvio del job Python su GitHub Actions. |
| `README.md` | Istruzioni manuali originali per configurare Supabase, repository e Actions. |

## Dettagli tecnici

### Script Python

- I ticker sono letti da `Anagrafica.csv` a ogni esecuzione; il file deve contenere la colonna `Ticker_yf` e usa `;` come separatore.
- `get_etf_price()` legge `Ticker.history(period="5d")`, fallisce se non riceve dati e prende l'ultimo valore di `Close`.
- La data salvata è `datetime.now().strftime("%Y-%m-%d")`, cioè la data corrente dell'ambiente di esecuzione, non la data della seduta a cui appartiene la chiusura recuperata. Nei fine settimana e nei giorni senza contrattazioni può quindi registrare lo stesso ultimo prezzo disponibile sotto date diverse.
- `save_to_db()` esegue un upsert sulla tabella `prices`, con conflitto sulla coppia `(date, ticker)`; in caso di conflitto aggiorna `price`.
- All'avvio lo script verifica che `psycopg2` sia installato e che `DATABASE_URL` sia impostata; in caso contrario stampa un errore esplicito e termina con codice 1. `psycopg2-binary` è già dichiarato in `requirements.txt`. La connessione/cursore vengono chiusi solo nel percorso senza eccezioni.

### Schema database atteso

Il codice attuale richiede una tabella PostgreSQL `prices` con almeno:

| Colonna | Requisito usato dal codice |
| --- | --- |
| `date` | Data, `NOT NULL`. |
| `ticker` | Testo non nullo. |
| `price` | Valore numerico. |
| `created_at` | Selezionata dal frontend tramite `select('*')`; utile con default UTC come nel DDL del README. |

La coppia `(date, ticker)` deve avere un vincolo `UNIQUE`, necessario a `ON CONFLICT (date, ticker)`. Il DDL è documentato nel README.

### GitHub Actions e segreti

- Workflow: `Daily ETF Tracker`.
- Trigger schedulato: cron `0 18 * * *` (tutti i giorni, 7 giorni su 7; non solo nei giorni feriali).
- Trigger manuale: `workflow_dispatch`.
- Runner: `ubuntu-latest`; versione Python fissata a `3.10`.
- Il workflow installa `requirements.txt` ed espone `secrets.DATABASE_URL` allo script solo nel passo di esecuzione.
- `DATABASE_URL` deve essere l'URI PostgreSQL Supabase completo. Se la password contiene caratteri speciali, il README ricorda che vanno percent-encoded nell'URI.

### Frontend e accesso Supabase

- `index.html` crea il client Supabase nel browser usando URL del progetto e chiave publishable/anon incorporati nel file. Una chiave publishable/anon è destinata all'uso client: non sostituirla con la service-role key o altre credenziali privilegiate.
- Il browser legge `prices`, seleziona tutte le colonne e ordina per `date` decrescente.
- Il caricamento è paginato con batch da 1000 record usando `.range(from, to)` e continua fino a ricevere meno di 1000 righe.
- Perché la lettura dal browser funzioni, Supabase deve consentire al ruolo anon la `SELECT` necessaria tramite impostazioni RLS/policy. Le policy effettive non sono presenti nel repository e vanno verificate nel progetto Supabase; non disabilitare RLS come scorciatoia.
- L'errore di lettura è scritto in console e mostrato nel contatore. I record sono inseriti nel DOM componendo `innerHTML` con data, ticker e prezzo: se questi campi diventano modificabili da fonti non fidate, usare inserimento testuale sicuro invece di interpolarli come HTML.
- Dipendenze browser caricate da CDN: Tailwind CSS, Chart.js, `chartjs-plugin-zoom` 2.2.0 e `@supabase/supabase-js` 2. Non è configurato un bundler o un sistema di pinning per tutte le librerie.
- Il grafico mostra una serie per ticker in ordine cronologico crescente; zoom e pan sono limitati all'asse X e il pulsante reset richiama `resetZoom()`.

## Setup e operatività descritti nel README

Il README guida la creazione manuale del progetto PostgreSQL su Supabase, della tabella `prices`, del repository GitHub, del secret Actions `DATABASE_URL` e del workflow. Specifica la pianificazione quotidiana alle 18:00 UTC, weekend inclusi, e suggerisce un test manuale dalla scheda Actions con verifica del record in Supabase. Le policy e le impostazioni effettive nei dashboard non sono verificabili dai soli file del repository.

## Esecuzione e verifica

Per eseguire localmente da PowerShell, installare le dipendenze con `python -m pip install -r requirements.txt`, impostare `DATABASE_URL` nell'ambiente e avviare `python tracker.py`; il README contiene i comandi. L'impostazione PowerShell vale per la finestra corrente. Non inserire la stringa di connessione nel repository o nella pagina HTML.

Al momento non sono presenti test automatizzati, file di configurazione di test, migrazioni database o workflow CI per test. Per una modifica al flusso dati verificare almeno: ticker e prezzo recuperati, schema e vincolo univoco, upsert idempotente, accesso anon in lettura e rendering nel browser.

## Considerazioni per le modifiche future

- Mantenere coerenti i nomi tabella/colonne tra script, pagina, schema Supabase e README.
- Per registrare più ticker nella stessa data, mantenere il vincolo composto `UNIQUE(date, ticker)` allineato con la clausola `ON CONFLICT(date, ticker)`.
- Decidere esplicitamente se la data deve indicare il giorno di esecuzione o la data effettiva dell'ultima seduta. La logica attuale usa il giorno di esecuzione.
- Valutare l'orario cron in UTC rispetto alle aspettative locali e ai cambi di ora legale; lo schedule GitHub non è espresso nel fuso italiano.
- Qualunque modifica a schema, policy RLS o credenziali va applicata e verificata anche sul progetto Supabase remoto: il repository non contiene migrazioni né policy.
- Evitare di committare segreti. Per cambiare accesso pubblico, usare la chiave publishable/anon con policy RLS minime e mantenere `DATABASE_URL` nei secret GitHub Actions.
