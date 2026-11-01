# Contesto del progetto

Questa guida riassume lo stato del repository e le dipendenze tra codice e infrastruttura per facilitare le modifiche future. È stata ricavata dai file presenti nel repository, in particolare dal [README.md](./README.md), e non verifica lo stato effettivo delle impostazioni nei dashboard GitHub o Supabase.

## Scopo e flusso dei dati

Il progetto registra lo storico dei prezzi e dei dividendi dei titoli elencati in `Anagrafica.csv`. Gli script interrogano Yahoo Finance con `Ticker_yf`, mentre il database e la pagina usano `Ticker` come identificativo canonico e mostrano `Descrizione`.

1. Il workflow GitHub Actions `.github/workflows/tracker.yml` esegue `tracker.py` alle 18:00 UTC ogni giorno e può essere avviato anche manualmente.
2. Gli script leggono `Ticker`, `Ticker_yf` e `Descrizione` da `Anagrafica.csv`; usano `Ticker_yf` per Yahoo Finance e sincronizzano ticker/descrizione in `instruments`.
3. `tracker.py` connette PostgreSQL su Supabase usando il secret Actions `DATABASE_URL` e aggiorna i prezzi recenti e gli eventi dividendo da Yahoo Finance.
4. `index.html` interroga Supabase direttamente dal browser usando la chiave pubblica, carica prezzi, dividendi, descrizioni e portafogli, e mostra grafico e posizioni filtrabili per portafoglio. Gli eventi dividendo sono indicati sul grafico con marcatori e tooltip. `index_old.html` è la versione di prova conservata come backup.

Non ci sono backend applicativi o framework di build: la UI è una pagina statica autonoma e lo script è un job batch.

## File e responsabilità

| File | Responsabilità |
| --- | --- |
| `tracker.py` | Lettura CSV, sincronizzazione anagrafica, recupero prezzi e dividendi Yahoo Finance e upsert nel database PostgreSQL. |
| `backfill_history.py` | Sincronizzazione anagrafica e caricamento manuale dello storico di prezzi e dividendi Yahoo Finance. |
| `index.html` | Pagina pubblicata con grafico affiancato all'elenco delle posizioni; le righe filtrano le serie visualizzate. |
| `index_old.html` | Versione di prova precedente, conservata nel repository come backup e non pubblicata. |
| `requirements.txt` | Dipendenze Python (`yfinance`, `psycopg2-binary`, `python-dotenv`). |
| `.env.example` / `.gitignore` | Esempio della configurazione locale e protezione dei file `.env` dai commit. |
| `.github/workflows/tracker.yml` | Pianificazione e avvio del job Python su GitHub Actions. |
| `.github/workflows/pages.yml` | Pubblicazione su GitHub Pages del solo `index.html`. |
| `README.md` | Istruzioni manuali originali per configurare Supabase, repository e Actions. |

## Dettagli tecnici

### Script Python

- `Anagrafica.csv` viene letto a ogni esecuzione con separatore `;` e deve contenere `Ticker`, `Ticker_yf` e `Descrizione`. `Ticker` deve essere univoco e entrambi i ticker devono essere valorizzati; una descrizione vuota è ammessa.
- `Ticker_yf` è usato solo per le richieste a Yahoo Finance. `Ticker` è la chiave canonica salvata in `prices.ticker` e `instruments.ticker`.
- Entrambi gli script sincronizzano in `instruments` il ticker e la descrizione del CSV tramite upsert. `tracker.py` esegue la sincronizzazione prima del ciclo dei prezzi; `backfill_history.py` sincronizza l'anagrafica e lo storico nella stessa transazione.
- `get_etf_prices()` richiede tramite `yfinance` le chiusure giornaliere non rettificate (`auto_adjust=False`) dell'ultimo mese per `Ticker_yf` e restituisce le date effettive comunicate da Yahoo Finance. I prezzi grezzi sono necessari per l'adeguamento dei dividendi mostrato nella pagina.
- `save_to_db()` verifica quali date recenti per ticker esistono già in `prices`: inserisce le date mancanti e aggiorna la quotazione odierna se disponibile. La finestra mensile consente di recuperare quotazioni aggiornate settimanalmente o pubblicate in ritardo, con la loro data effettiva; nei giorni senza nuove quotazioni non vengono creati record artificiali.
- `save_to_db()` usa un upsert sulla tabella `prices`, con conflitto sulla coppia `(date, ticker)`; in caso di conflitto aggiorna `price`.
- `get_etf_dividends()` legge la serie storica `Ticker.dividends` fornita da yfinance. `save_dividends_to_db()` salva gli eventi in `dividends` con upsert su `(date, ticker)`, aggiornando importi eventualmente corretti da Yahoo Finance.
- All'avvio lo script verifica che `psycopg2` sia installato e che `DATABASE_URL` sia impostata; in caso contrario stampa un errore esplicito e termina con codice 1. `psycopg2-binary` è già dichiarato in `requirements.txt`. La connessione/cursore vengono chiusi solo nel percorso senza eccezioni.
- `backfill_history.py` è uno script manuale separato: per ogni `Ticker_yf` usa `Ticker.history(start="2022-01-01", auto_adjust=False)` per i prezzi e `Ticker.dividends` per i dividendi, filtrando entrambi dal 1° gennaio 2022 incluso. Se per un ticker non trova prezzi validi, stampa un avviso, raccoglie comunque gli eventuali dividendi e continua con i ticker successivi. Salva le chiusure `Close` con le date effettive e i dividendi alle date fornite da Yahoo inserendo solo righe mancanti con `ON CONFLICT DO NOTHING`; non modifica prezzi o importi già presenti. Dopo aver elaborato i ticker sincronizza anagrafica, prezzi e dividendi disponibili in un'unica transazione.
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

La pagina usa anche `portfolios` (`id` intero identity, `name` univoco) e `portfolio_positions` (chiave primaria composta `(portfolio_id, ticker)`, quantità `numeric(20, 8)` positiva e prezzo medio di acquisto `average_purchase_price numeric(20, 8)` positivo e nullable). Le posizioni fanno riferimento a `portfolios.id` e `instruments.ticker`; eliminare un portafoglio elimina le sue posizioni. Le due tabelle non hanno date di creazione. Le policy RLS documentate nel README consentono al ruolo `anon` solo la lettura: con esse i nomi, i ticker, le quantità e i prezzi medi di acquisto dei portafogli sono pubblici.

### GitHub Actions e segreti

- Workflow: `Daily ETF Tracker`.
- Trigger schedulato: cron `0 18 * * *` (tutti i giorni, 7 giorni su 7; non solo nei giorni feriali).
- Trigger manuale: `workflow_dispatch`.
- Runner: `ubuntu-latest`; versione Python fissata a `3.10`.
- Il workflow installa `requirements.txt` ed espone `secrets.DATABASE_URL` allo script solo nel passo di esecuzione.
- `DATABASE_URL` deve essere l'URI PostgreSQL Supabase completo. Se la password contiene caratteri speciali, il README ricorda che vanno percent-encoded nell'URI.
- `.github/workflows/pages.yml` pubblica su push a `main` o avvio manuale; l'artefatto contiene solo `index.html`. In GitHub Pages la sorgente va impostata su **GitHub Actions**.

### Frontend e accesso Supabase

- `index.html` crea il client Supabase nel browser usando URL del progetto e chiave publishable/anon incorporati nel file. Una chiave publishable/anon è destinata all'uso client: non sostituirla con la service-role key o altre credenziali privilegiate. `index_old.html` è una versione di prova tenuta come backup.
- La pagina offre un pulsante per alternare tema chiaro e scuro; la scelta è salvata nel `localStorage` del browser e aggiorna anche colori di assi, griglia e legenda del grafico.
- Il browser legge `instruments` (ticker e descrizione) e `portfolios`; per il portafoglio selezionato legge ticker, quantità e prezzo medio di acquisto da `portfolio_positions`, e carica i relativi prezzi da `prices` e i dividendi da `dividends`. Il ruolo anon necessita di policy RLS in sola lettura su tutte e cinque le tabelle; importi dei dividendi e prezzi medi di acquisto per i ticker in portafoglio sono quindi pubblicamente leggibili.
- Il caricamento di prezzi, posizioni e dividendi è paginato con batch da 1000 record usando `.range(from, to)` e continua fino a ricevere meno di 1000 righe.
- Perché la lettura dal browser funzioni, Supabase deve consentire al ruolo anon la `SELECT` necessaria tramite impostazioni RLS/policy. Le policy effettive non sono presenti nel repository e vanno verificate nel progetto Supabase; non disabilitare RLS come scorciatoia.
- Gli errori di lettura sono scritti in console e mostrati sotto la tabella riepilogativa. Le righe delle tabelle sono costruite con elementi DOM e `textContent` per mostrare i dati senza interpretarli come HTML.
- Dipendenze browser caricate da CDN: Tailwind CSS, Chart.js, Hammer.js 2.0.8 (richiesto dal pan), `chartjs-plugin-zoom` 2.2.0 e `@supabase/supabase-js` 2. Non è configurato un bundler o un sistema di pinning per tutte le librerie.
- Il selettore offre solo i portafogli disponibili: all'avvio viene selezionato il primo ordinato per nome e la pagina non mostra mai la vista con tutti i ticker. Prezzi e posizioni vengono caricati solo per il portafoglio selezionato. La tabella mostra i ticker assegnati e le quantità, includendo posizioni senza prezzi registrati; il grafico mostra solo ticker con storico prezzi. Se non ci sono portafogli, il selettore resta disabilitato e la pagina invita a crearne uno. Il riepilogo mostra inoltre descrizione, prezzo più recente, variazione percentuale giornaliera e variazione percentuale rispetto al prezzo medio di acquisto (se disponibile); ciascuna colonna è ordinabile.
- Per ogni dividendo del portafoglio selezionato, un plugin Chart.js disegna un marcatore `D` sopra l'asse X alla data dell'evento, nel colore della serie ticker. I marcatori seguono filtro portafoglio, visibilità della serie e intervallo temporale del grafico; il tooltip mostra la data su una riga e ticker e importo con formato `TICKER: 0,00 €` sulla successiva. Non è presente una tabella storica dei dividendi.
- Il pulsante `Total return`, accanto ai controlli prezzo/percentuale, adegua i prezzi storici per i dividendi: per ogni evento applica alle quotazioni precedenti il fattore `(prezzo di chiusura precedente - dividendo) / prezzo di chiusura precedente`. Il prezzo più recente resta invariato; la modalità percentuale normalizza i valori già adeguati. Un dividendo non applicabile viene escluso dal calcolo e segnalato sotto al grafico.
- Non è visualizzata una tabella dello storico prezzi; i record dei prezzi restano caricati per costruire il grafico e calcolare gli intervalli.
- Il grafico mostra una serie per ticker in ordine cronologico crescente su un asse giornaliero continuo tra la prima e l'ultima data, anche quando mancano record per alcuni giorni; le linee collegano i prezzi disponibili attraverso i giorni vuoti. Ogni ticker riceve una tonalità distinta generata dal suo indice nella lista ordinata, con luminosità adattata al tema. All'avvio è visibile solo la prima serie e l'intervallo visualizzato copre le date disponibili per quel ticker. La legenda usa marcatori quadrati compatti. Il pulsante `Percentuale` normalizza ogni serie sul valore disponibile alla prima data visualizzata (base 0%); se quel giorno non ha una quotazione, usa l'ultimo valore precedente e mostra un punto base a 0% all'inizio dell'intervallo. Se non esistono valori precedenti, usa il primo successivo disponibile. La normalizzazione si aggiorna quando cambia l'intervallo con zoom, pan o pulsanti periodo, anche quando è attiva la modalità total return. In modalità percentuale le linee sono lineari; `Valori assoluti` ripristina prezzi e curvatura originali. I pulsanti `1M`, `1Y`, `YTD` e `Tutto` mostrano rispettivamente gli ultimi 30 o 365 giorni, il periodo dall'inizio dell'anno (o dall'ultimo record precedente disponibile) oppure, per `Tutto`, l'intervallo coperto dai dati dei ticker attualmente visibili; mentre `Tutto` è selezionato, l'intervallo si aggiorna anche quando cambia la visibilità delle serie. La variazione percentuale tra primo e ultimo prezzo visibili è mostrata per ogni serie attiva e si aggiorna con zoom, pan e visibilità delle serie. Le etichette dell'asse X si adattano allo zoom: giorni e marcatori mensili/annuali da vicino, solo mese/anno sulle viste ampie; gli intervalli sono calcolati sulle date calendariali, non sulla presenza di record. Il grafico è più alto per impostazione predefinita e il riquadro può essere espanso a schermo intero. L'area sensibile dei punti è estesa per facilitare i tooltip; zoom e pan sono limitati all'asse X.
- In modalità percentuale, l'etichetta di variazione in alto coincide con l'ultimo valore normalizzato del grafico; i tooltip mostrano i valori con due decimali e l'unità coerente con la modalità (percentuale o euro).
- `index.html` riutilizza le letture Supabase e i dati dei portafogli, mostra solo l'elenco compatto delle posizioni affiancato al grafico, senza intestazioni dei pannelli né note sotto grafico e tabella, con ticker, prezzo, variazione giornaliera e variazione rispetto al prezzo medio di acquisto (se disponibile) in colonne separate e ordinabili, oltre alla selezione multipla delle serie. Il grafico usa linee da 1,25 px e marker circolari colorati nel tooltip, leggermente sollevati rispetto al testo, con 8 px di spazio prima del testo. Quando il tooltip mostra più serie, ticker e valori sono allineati in colonne usando il font monospaziato. Sopra al grafico mostra a sinistra le serie attive con la variazione percentuale tra il primo e l'ultimo prezzo visibili nell'intervallo scelto; in modalità percentuale usa il valore normalizzato finale. Non mostra la riga dell'intervallo di date. Le etichette degli assi e il tooltip usano font monospaziato a 9 px, come le etichette delle serie. Il selettore del portafoglio mostra etichetta e menu sulla stessa riga e ha la stessa altezza del pulsante tema. I controlli del grafico hanno spaziatura superiore ridotta: gli intervalli sono seguiti da un selettore segmentato `Prezzo (€)`/`Percentuale (%)` e da un pulsante per attivare lo schermo intero del grafico; tutti i pulsanti hanno altezza di 30 px e dimensioni coerenti con i controlli intervallo. L'icona a schermo intero passa a frecce rivolte verso il centro quando la visualizzazione è espansa. Include intervalli `1M`/`1A`/`YTD`/`Tutto`, modalità prezzo/percentuale e tema condiviso tramite `localStorage`. Il grafico carica i dividendi del portafoglio e disegna un marker `D` per ogni evento sulla data calendariale, nel colore della serie ticker; i marker seguono visibilità del ticker e intervallo temporale e mostrano un tooltip nello stile del grafico prezzi, con data in alto e marker circolare colorato accanto a ticker e importo per azione. Il pan orizzontale si esegue trascinando il grafico; lo zoom è disponibile con rotellina e pinch su asse X e aggiorna la normalizzazione percentuale e le variazioni mostrate. Le dipendenze browser per queste interazioni sono Hammer.js 2.0.8 e `chartjs-plugin-zoom` 2.2.0, caricate da CDN. La barra superiore compatta occupa 58 px su desktop e 54 px su mobile; su desktop la griglia si adatta all'altezza del viewport, mentre sui dispositivi stretti le sezioni scorrono verticalmente.

## Setup e operatività descritti nel README

Il README guida la creazione manuale del progetto PostgreSQL su Supabase, delle tabelle `instruments`, `prices`, `dividends`, `portfolios` e `portfolio_positions`, del repository GitHub, del secret Actions `DATABASE_URL` e dei workflow. Documenta policy RLS in sola lettura per le tabelle consultate dal browser, inclusa la lettura pubblica degli importi dividendo necessari ai marker grafici, la pianificazione quotidiana alle 18:00 UTC, weekend inclusi, il backfill iniziale e la pubblicazione del solo `index.html` con GitHub Pages; `index_old.html` resta come backup. Le policy e le impostazioni effettive nei dashboard non sono verificabili dai soli file del repository.

Per il caricamento iniziale, il README documenta l'esecuzione manuale di `backfill_history.py` da PowerShell con `DATABASE_URL`; lo script usa lo storico massimo disponibile su Yahoo Finance e scrive l'intero lotto disponibile in modo transazionale. Se un ticker non ha prezzi validi, segnala il caso e prosegue con il successivo. Prezzi e dividendi esistenti sono preservati e vengono inserite solo le date mancanti.

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
