# Contesto del progetto

Questa guida riassume lo stato del repository e le dipendenze tra codice e infrastruttura per facilitare le modifiche future. È stata ricavata dai file presenti nel repository, in particolare dal [README.md](./README.md), e non verifica lo stato effettivo delle impostazioni nei dashboard GitHub o Supabase.

## Scopo e flusso dei dati

Il progetto registra lo storico dei prezzi e dei dividendi dei titoli elencati in `Anagrafica.csv`. Gli script interrogano Yahoo Finance con `Ticker_yf`, mentre il database e la pagina usano `Ticker` come identificativo canonico e mostrano `Descrizione`.

1. Il workflow GitHub Actions `.github/workflows/tracker.yml` esegue `tracker.py` alle 18:00 UTC ogni giorno e può essere avviato anche manualmente.
2. Gli script leggono `Ticker`, `Ticker_yf` e `Descrizione` da `Anagrafica.csv`; usano `Ticker_yf` per Yahoo Finance e sincronizzano ticker/descrizione in `instruments`.
3. `tracker.py` connette PostgreSQL su Supabase usando il secret Actions `DATABASE_URL` e aggiorna i prezzi recenti e gli eventi dividendo da Yahoo Finance.
4. `index.html` interroga Supabase direttamente dal browser usando la chiave pubblica, carica prezzi, dividendi, descrizioni e portafogli, e mostra riepilogo e grafico prezzi filtrabili per portafoglio. Gli eventi dividendo sono indicati sul grafico con marcatori e tooltip, senza una tabella storica.

Non ci sono backend applicativi o framework di build: la UI è una pagina statica autonoma e lo script è un job batch.

## File e responsabilità

| File | Responsabilità |
| --- | --- |
| `tracker.py` | Lettura CSV, sincronizzazione anagrafica, recupero prezzi e dividendi Yahoo Finance e upsert nel database PostgreSQL. |
| `backfill_history.py` | Sincronizzazione anagrafica e caricamento manuale dello storico di prezzi e dividendi Yahoo Finance. |
| `index.html` | Pagina italiana con riepilogo, grafico prezzi e marcatori di dividendo filtrati per portafoglio. |
| `requirements.txt` | Dipendenze Python (`yfinance`, `psycopg2-binary`, `python-dotenv`). |
| `.env.example` / `.gitignore` | Esempio della configurazione locale e protezione dei file `.env` dai commit. |
| `.github/workflows/tracker.yml` | Pianificazione e avvio del job Python su GitHub Actions. |
| `README.md` | Istruzioni manuali originali per configurare Supabase, repository e Actions. |

## Dettagli tecnici

### Script Python

- `Anagrafica.csv` viene letto a ogni esecuzione con separatore `;` e deve contenere `Ticker`, `Ticker_yf` e `Descrizione`. `Ticker` deve essere univoco e entrambi i ticker devono essere valorizzati; una descrizione vuota è ammessa.
- `Ticker_yf` è usato solo per le richieste a Yahoo Finance. `Ticker` è la chiave canonica salvata in `prices.ticker` e `instruments.ticker`.
- Entrambi gli script sincronizzano in `instruments` il ticker e la descrizione del CSV tramite upsert. `tracker.py` esegue la sincronizzazione prima del ciclo dei prezzi; `backfill_history.py` sincronizza l'anagrafica e lo storico nella stessa transazione.
- `get_etf_prices()` richiede tramite `yfinance` le chiusure giornaliere dell'ultimo mese per `Ticker_yf` e restituisce le date effettive comunicate da Yahoo Finance.
- `save_to_db()` verifica quali date recenti per ticker esistono già in `prices`: inserisce le date mancanti e aggiorna la quotazione odierna se disponibile. La finestra mensile consente di recuperare quotazioni aggiornate settimanalmente o pubblicate in ritardo, con la loro data effettiva; nei giorni senza nuove quotazioni non vengono creati record artificiali.
- `save_to_db()` usa un upsert sulla tabella `prices`, con conflitto sulla coppia `(date, ticker)`; in caso di conflitto aggiorna `price`.
- `get_etf_dividends()` legge la serie storica `Ticker.dividends` fornita da yfinance. `save_dividends_to_db()` salva gli eventi in `dividends` con upsert su `(date, ticker)`, aggiornando importi eventualmente corretti da Yahoo Finance.
- All'avvio lo script verifica che `psycopg2` sia installato e che `DATABASE_URL` sia impostata; in caso contrario stampa un errore esplicito e termina con codice 1. `psycopg2-binary` è già dichiarato in `requirements.txt`. La connessione/cursore vengono chiusi solo nel percorso senza eccezioni.
- `backfill_history.py` è uno script manuale separato: per ogni `Ticker_yf` usa `Ticker.history(start="2022-01-01", auto_adjust=False)` per i prezzi e `Ticker.dividends` per i dividendi, filtrando entrambi dal 1° gennaio 2022 incluso. Salva le chiusure `Close` con le date effettive e i dividendi alle date fornite da Yahoo, tramite upsert sulle rispettive chiavi `(date, ticker)`. Scarica e valida tutti i ticker prima di sincronizzare anagrafica, prezzi e dividendi in un'unica transazione, così un errore di download non lascia un caricamento parziale.
- Entrambi gli script caricano `DATABASE_URL` dal file `.env` nella cartella del progetto tramite `python-dotenv`, senza sovrascrivere un valore già presente nell'ambiente. `.env` è escluso da Git; `.env.example` contiene solo un URI segnaposto.

### Schema database atteso

Il database richiede una tabella PostgreSQL `instruments` con `ticker` come chiave primaria e `description` nullable. `prices.ticker` è una chiave esterna a `instruments.ticker`. La tabella `prices` richiede inoltre almeno:

| Colonna | Requisito usato dal codice |
| --- | --- |
| `date` | Data, `NOT NULL`. |
| `ticker` | Testo non nullo, chiave esterna a `instruments.ticker`. |
| `price` | Valore numerico. |
| `created_at` | Selezionata dal frontend tramite `select('*')`; utile con default UTC come nel DDL del README. |

La coppia `(date, ticker)` deve avere un vincolo `UNIQUE`, necessario a `ON CONFLICT (date, ticker)`. Il DDL è documentato nel README.

Il database richiede inoltre `dividends` con `date`, `ticker` (chiave esterna a `instruments.ticker`) e `amount` positivo `numeric(20, 8)`, con chiave primaria composta `(date, ticker)`. `amount` è il dividendo per azione riportato da Yahoo Finance; la data non va interpretata automaticamente come data di pagamento. Il DDL e le policy RLS sono documentati nel README.

La pagina usa anche `portfolios` (`id` intero identity, `name` univoco) e `portfolio_positions` (chiave primaria composta `(portfolio_id, ticker)`, quantità `numeric(20, 8)` positiva). Le posizioni fanno riferimento a `portfolios.id` e `instruments.ticker`; eliminare un portafoglio elimina le sue posizioni. Le due tabelle non hanno date di creazione. Le policy RLS documentate nel README consentono al ruolo `anon` solo la lettura: con esse i nomi, i ticker e le quantità dei portafogli sono pubblici.

### GitHub Actions e segreti

- Workflow: `Daily ETF Tracker`.
- Trigger schedulato: cron `0 18 * * *` (tutti i giorni, 7 giorni su 7; non solo nei giorni feriali).
- Trigger manuale: `workflow_dispatch`.
- Runner: `ubuntu-latest`; versione Python fissata a `3.10`.
- Il workflow installa `requirements.txt` ed espone `secrets.DATABASE_URL` allo script solo nel passo di esecuzione.
- `DATABASE_URL` deve essere l'URI PostgreSQL Supabase completo. Se la password contiene caratteri speciali, il README ricorda che vanno percent-encoded nell'URI.

### Frontend e accesso Supabase

- `index.html` crea il client Supabase nel browser usando URL del progetto e chiave publishable/anon incorporati nel file. Una chiave publishable/anon è destinata all'uso client: non sostituirla con la service-role key o altre credenziali privilegiate.
- La pagina offre un pulsante per alternare tema chiaro e scuro; la scelta è salvata nel `localStorage` del browser e aggiorna anche colori di assi, griglia e legenda del grafico.
- Il browser legge `instruments` (ticker e descrizione) e `portfolios`; per il portafoglio selezionato legge le righe da `portfolio_positions` e carica i relativi prezzi da `prices` e i dividendi da `dividends`. Il ruolo anon necessita di policy RLS in sola lettura su tutte e cinque le tabelle; gli importi dei dividendi per i ticker in portafoglio sono quindi pubblicamente leggibili.
- Il caricamento di prezzi, posizioni e dividendi è paginato con batch da 1000 record usando `.range(from, to)` e continua fino a ricevere meno di 1000 righe.
- Perché la lettura dal browser funzioni, Supabase deve consentire al ruolo anon la `SELECT` necessaria tramite impostazioni RLS/policy. Le policy effettive non sono presenti nel repository e vanno verificate nel progetto Supabase; non disabilitare RLS come scorciatoia.
- Gli errori di lettura sono scritti in console e mostrati sotto la tabella riepilogativa. Le righe delle tabelle sono costruite con elementi DOM e `textContent` per mostrare i dati senza interpretarli come HTML.
- Dipendenze browser caricate da CDN: Tailwind CSS, Chart.js, Hammer.js 2.0.8 (richiesto dal pan), `chartjs-plugin-zoom` 2.2.0 e `@supabase/supabase-js` 2. Non è configurato un bundler o un sistema di pinning per tutte le librerie.
- Il selettore offre solo i portafogli disponibili: all'avvio viene selezionato il primo ordinato per nome e la pagina non mostra mai la vista con tutti i ticker. Prezzi e posizioni vengono caricati solo per il portafoglio selezionato. La tabella mostra i ticker assegnati e le quantità, includendo posizioni senza prezzi registrati; il grafico mostra solo ticker con storico prezzi. Se non ci sono portafogli, il selettore resta disabilitato e la pagina invita a crearne uno. Il riepilogo mostra inoltre descrizione, prezzo più recente e variazione percentuale rispetto al record con la data precedente; ciascuna colonna è ordinabile.
- Per ogni dividendo del portafoglio selezionato, un plugin Chart.js disegna un marcatore `D` sopra l'asse X alla data dell'evento, nel colore della serie ticker. I marcatori seguono filtro portafoglio, visibilità della serie e intervallo temporale del grafico; il tooltip mostra la data su una riga e ticker e importo con formato `TICKER: 0,00 €` sulla successiva. Non è presente una tabella storica dei dividendi.
- Non è visualizzata una tabella dello storico prezzi; i record dei prezzi restano caricati per costruire il grafico e calcolare gli intervalli.
- Il grafico mostra una serie per ticker in ordine cronologico crescente su un asse giornaliero continuo tra la prima e l'ultima data, anche quando mancano record per alcuni giorni; le linee collegano i prezzi disponibili attraverso i giorni vuoti. Ogni ticker riceve una tonalità distinta generata dal suo indice nella lista ordinata, con luminosità adattata al tema. All'avvio è visibile solo la prima serie e l'intervallo visualizzato copre le date disponibili per quel ticker. La legenda usa marcatori quadrati compatti. Il pulsante `Percentuale` normalizza ogni serie sul valore interpolato al bordo sinistro dell'intervallo temporale visualizzato (base 0%) e mostra le variazioni relative con etichette percentuali sull'asse Y; in questa modalità le linee sono lineari per allineare esattamente il bordo a zero. `Valori assoluti` ripristina prezzi e curvatura originali. La normalizzazione si aggiorna quando cambia l'intervallo con zoom, pan o pulsanti periodo. I pulsanti `1M`, `1Y`, `YTD` e `Tutto` mostrano rispettivamente gli ultimi 30 o 365 giorni, il periodo dall'inizio dell'anno (o dall'ultimo record precedente disponibile) oppure, per `Tutto`, l'intervallo coperto dai dati dei ticker attualmente visibili; mentre `Tutto` è selezionato, l'intervallo si aggiorna anche quando cambia la visibilità delle serie. La variazione percentuale tra primo e ultimo prezzo visibili è mostrata per ogni serie attiva e si aggiorna con zoom, pan e visibilità delle serie. Le etichette dell'asse X si adattano allo zoom: giorni e marcatori mensili/annuali da vicino, solo mese/anno sulle viste ampie; gli intervalli sono calcolati sulle date calendariali, non sulla presenza di record. Il grafico è più alto per impostazione predefinita e il riquadro può essere espanso a schermo intero. L'area sensibile dei punti è estesa per facilitare i tooltip; zoom e pan sono limitati all'asse X.
- In modalità percentuale, l'etichetta di variazione in alto coincide con l'ultimo valore normalizzato del grafico; i tooltip mostrano i valori con due decimali e l'unità coerente con la modalità (percentuale o euro).

## Setup e operatività descritti nel README

Il README guida la creazione manuale del progetto PostgreSQL su Supabase, delle tabelle `instruments`, `prices`, `dividends`, `portfolios` e `portfolio_positions`, del repository GitHub, del secret Actions `DATABASE_URL` e del workflow. Documenta policy RLS in sola lettura per le tabelle consultate dal browser, inclusa la lettura pubblica degli importi dividendo necessari ai marker grafici, la pianificazione quotidiana alle 18:00 UTC, weekend inclusi, e il backfill iniziale. Le policy e le impostazioni effettive nei dashboard non sono verificabili dai soli file del repository.

Per il caricamento iniziale, il README documenta l'esecuzione manuale di `backfill_history.py` da PowerShell con `DATABASE_URL`; lo script usa lo storico massimo disponibile su Yahoo Finance e scrive l'intero lotto in modo transazionale.

## Esecuzione e verifica

Per eseguire localmente da PowerShell, installare le dipendenze con `python -m pip install -r requirements.txt`, creare `.env` a partire da `.env.example`, inserire `DATABASE_URL` e avviare `python tracker.py` o `python backfill_history.py`; i file `.env` sono ignorati da Git. Non inserire la stringa di connessione nel repository o nella pagina HTML.

Al momento non sono presenti test automatizzati, file di configurazione di test, migrazioni database o workflow CI per test. Per una modifica al flusso dati verificare almeno: ticker, prezzi e dividendi recuperati, schema e vincoli univoci, upsert idempotente, accesso anon in lettura e rendering nel browser.

## Considerazioni per le modifiche future

- Mantenere coerenti i nomi tabella/colonne tra script, pagina, schema Supabase e README.
- I portafogli sono pubblicamente leggibili tramite la policy `anon` descritta nel README; se diventano personali, associare un proprietario e sostituire le policy pubbliche con policy RLS per utente autenticato.
- Per registrare più ticker nella stessa data, mantenere il vincolo composto `UNIQUE(date, ticker)` allineato con la clausola `ON CONFLICT(date, ticker)`.
- Il job giornaliero recupera le quotazioni mancanti tra quelle disponibili nell'ultimo mese Yahoo Finance, registrandole alle date delle rispettive sedute; in giornate senza contrattazioni non crea record artificiali.
- Valutare l'orario cron in UTC rispetto alle aspettative locali e ai cambi di ora legale; lo schedule GitHub non è espresso nel fuso italiano.
- Qualunque modifica a schema, policy RLS o credenziali va applicata e verificata anche sul progetto Supabase remoto: il repository non contiene migrazioni né policy.
- Evitare di committare segreti. Per cambiare accesso pubblico, usare la chiave publishable/anon con policy RLS minime e mantenere `DATABASE_URL` nei secret GitHub Actions.
