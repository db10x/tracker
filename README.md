# tracker
script giornaliero per scaricare i prezzi dei titoli su DB (Supabase)


## Passo 1: Creare il database su Supabase 
Supabase ti offre un database PostgreSQL nel cloud in pochi secondi.
Vai su supabase.com e registrati (puoi usare direttamente il tuo account GitHub).
Clicca su "New Project".
Scegli un nome per il progetto (es. etf-tracker), imposta una password sicura per il database e seleziona una regione vicina (es. Frankfurt o EU West). Clicca su "Create new project" (ci vorrà circa un minuto per l'attivazione).
Una volta dentro la dashboard, vai sull'icona del database (SQL Editor nel menu a sinistra) e clicca su "New Query".
Incolla questo codice SQL per creare la tabella che conterrà i tuoi dati e premi "Run":

```sql
create table instruments (
    ticker text primary key,
    description text
);

create table prices (
    id serial primary key,
    date date not null,
    ticker text not null references instruments (ticker),
    price numeric(10, 2) not null,
    created_at timestamp with time zone default timezone('utc'::text, now()) not null,
    unique (date, ticker)
);

create table dividends (
    date date not null,
    ticker text not null references instruments (ticker),
    amount numeric(20, 8) not null check (amount > 0),
    primary key (date, ticker)
);
```

La tabella `instruments` contiene il ticker canonico e la descrizione; `prices.ticker`, `dividends.ticker` e `portfolio_positions.ticker` lo usano come chiave esterna. `dividends` memorizza l'importo per azione e la data dell'evento riportata da Yahoo Finance, non l'incasso personale né necessariamente la data di pagamento. Gli script mantengono `Anagrafica.csv` come fonte dell'anagrafica: se `Ticker_yf` è valorizzato interrogano Yahoo Finance, altrimenti usano `Ticker_EODHD` per recuperare le chiusure EODHD. `Ticker` è il ticker canonico salvato nel database. EODHD non viene usato per i dividendi. `backfill_history.py` inizializza/aggiorna `instruments` insieme allo storico; il job giornaliero sincronizza le descrizioni prima di salvare prezzi e dividendi. Mantieni il CSV nella stessa cartella degli script e includilo nel repository, così sarà disponibile anche in GitHub Actions.


Per aggiungere la gestione dei portafogli, esegui anche questo SQL nel SQL Editor. Su un database che contiene già `instruments` e `prices`, esegui solo questo blocco aggiuntivo, senza ricreare le tabelle esistenti:

```sql
create table public.portfolios (
    id integer generated always as identity primary key,
    name text not null unique check (btrim(name) <> '')
);

create table public.portfolio_positions (
    portfolio_id integer not null
        references public.portfolios (id) on delete cascade,
    ticker text not null
        references public.instruments (ticker),
    quantity numeric(20, 8) not null check (quantity > 0),
    average_purchase_price numeric(20, 8) check (average_purchase_price > 0),
    primary key (portfolio_id, ticker)
);
```

`portfolios` contiene un record per portafoglio, identificato da un ID intero assegnato automaticamente; `portfolio_positions` contiene una riga per ticker e portafoglio, con quantità posseduta e prezzo medio di acquisto. La chiave primaria composta impedisce duplicati dello stesso ticker nel portafoglio. Per aggiungere più titoli dello stesso ticker, aggiorna la quantità e il prezzo medio della riga esistente. `quantity` accetta frazioni di titolo; `average_purchase_price` è facoltativo e deve essere positivo: finché non è valorizzato, la variazione rispetto all'acquisto non viene mostrata. Se usi esclusivamente titoli interi, puoi sostituire `quantity` con `integer`. Le due tabelle non includono date di creazione, non necessarie alla pagina.

Nella pagina, il menu consente di scegliere solo un portafoglio: all'avvio viene selezionato il primo in ordine alfabetico e vengono caricati solo i suoi prezzi e le sue posizioni. Se non ci sono portafogli, la pagina chiede di crearne uno invece di mostrare tutti i ticker.

La pagina web visualizza i dati con la chiave pubblica Supabase: abilita RLS e consenti al ruolo `anon` solo la lettura delle cinque tabelle usate dalla pagina. Nomi dei portafogli, posizioni, quantità, prezzi medi di acquisto e importi dei dividendi dei ticker presenti nei portafogli saranno leggibili pubblicamente. La pagina mostra la variazione percentuale rispetto al prezzo medio di acquisto quando il dato è disponibile e non mostra una tabella dei dividendi: visualizza solo un marcatore `D` sul grafico per ciascun evento, con tooltip sull'importo per azione. Non concedere permessi di scrittura a `anon`. Per un database nuovo, applica:

```sql
alter table public.instruments enable row level security;
alter table public.prices enable row level security;
alter table public.portfolios enable row level security;
alter table public.portfolio_positions enable row level security;
alter table public.dividends enable row level security;

grant select on public.instruments, public.prices, public.portfolios, public.portfolio_positions, public.dividends to anon;

create policy "Allow anonymous read access to instruments"
    on public.instruments for select to anon using (true);
create policy "Allow anonymous read access to prices"
    on public.prices for select to anon using (true);
create policy "Allow anonymous read access to dividends"
    on public.dividends for select to anon using (true);
create policy "Allow anonymous read access to portfolios"
    on public.portfolios for select to anon using (true);
create policy "Allow anonymous read access to portfolio positions"
    on public.portfolio_positions for select to anon using (true);
```

Le righe di `portfolio_positions` devono riferirsi a ticker già presenti in `instruments`. Le modifiche all'anagrafica vengono effettuate dagli script usando `DATABASE_URL`; crea o modifica portafogli e posizioni da Supabase con un ruolo autorizzato, non dalla chiave anon della pagina.

Se hai già applicato le policy anon per `instruments` e `prices`, non rieseguire quelle istruzioni: applica solo le seguenti per le nuove tabelle.

```sql
alter table public.portfolios enable row level security;
alter table public.portfolio_positions enable row level security;
alter table public.dividends enable row level security;

grant select on public.portfolios, public.portfolio_positions, public.dividends to anon;

create policy "Allow anonymous read access to portfolios"
    on public.portfolios for select to anon using (true);
create policy "Allow anonymous read access to portfolio positions"
    on public.portfolio_positions for select to anon using (true);
create policy "Allow anonymous read access to dividends"
    on public.dividends for select to anon using (true);
```

Ora recuperiamo le credenziali: premi il tasto Connect nel menu in alto. Nella finestra che si apre a destra seleziona Direct Connection string e come connection method Connection pooler e Type URI. Copia la connection string (sarà simile a postgresql://postgres.wbwwjwfhgvrwtoaitleh:[YOUR-PASSWORD]@aws-0-eu-west-1.pooler.supabase.com:6543/postgres). Conserva questa stringa, ti servirà tra poco.

## Passo 2: Creare il Repository su GitHub
Vai su github.com e crea un New repository.
Dagli un nome (es. etf-tracker).
Impostalo come Private (così i tuoi dati e le configurazioni rimangono riservati).
Spunta la casella "Add a README file" e clicca su "Create repository".

## Passo 3: Scrivere lo Script Python nel Repository
Nel tuo nuovo repository su GitHub, creiamo i file necessari. Puoi farlo direttamente dal browser cliccando su "Add file" > "Create new file".

### 1. Crea il file requirements.txt
Questo file serve a dire a GitHub quali librerie Python installare.
Nome del file: requirements.txt
Contenuto:
Plaintext
yfinance
psycopg2-binary
Clicca su "Commit changes..." in alto a destra.

### 2. Crea il file tracker.py
Questo è lo script che farà tutto il lavoro.
Clicca su "Add file" > "Create new file" e chiamalo tracker.py.
Incolla il codice 

## Passo 4: Configurare il "Segreto" su GitHub (La password del DB)
Nel tuo repository GitHub, vai su Settings (Impostazioni) in alto.
Nel menu a sinistra, espandi Secrets and variables e clicca su Actions.
Clicca sul pulsante verde "New repository secret".
In Name scrivi: DATABASE_URL
In Secret incolla la stringa di connessione a Supabase postgresql://postgres.wbwwjwfhgvrwtoaitleh:[YOUR-PASSWORD]@aws-0-eu-west-1.pooler.supabase.com:6543/postgres (ricordati di sostituire [YOUR-PASSWORD] con la vera password che hai scelto quando hai creato il progetto su Supabase gestendo i caratteri speciali come spiegato di seguito. Ad es. # va sostituito con %23).
Clicca "Add secret".

Attenzione
Special symbols in passwords#
If you use special symbols in your Postgres password, you must remember to [percent-encode](https://en.wikipedia.org/wiki/Percent-encoding) your password later if using the Postgres connection string, for example, postgresql://postgres.projectref:p%3Dword@aws-0-us-east-1.pooler.supabase.com:6543/postgres


Se usi EODHD, crea anche il secret `EODHD_API_TOKEN` e inserisci il token API del tuo account. È necessario solo per le righe con `Ticker_yf` vuoto e `Ticker_EODHD` valorizzato.


## Passo 5: Creare l'automazione (GitHub Action)
Ora diciamo a GitHub quando far girare lo script. Il workflow pianifica l'esecuzione ogni giorno alle 18:00 UTC, weekend inclusi, e consente anche l'avvio manuale. Creeremo una cartella e un file di configurazione.
Torna nella pagina principale del tuo repository e clicca su "Add file" > "Create new file".
Come nome del file scrivi esattamente: .github/workflows/tracker.yml (creerà automaticamente la struttura di cartelle).
Incolla il codice YAML
Clicca su "Commit changes...".

## Passo 6: Testiamo se funziona!
Non devi aspettare domani sera per vedere se tutto è configurato correttamente: puoi fare un test manuale immediato.
Nel tuo repository GitHub, clicca sulla tab Actions in alto.
Sulla sinistra vedrai il flusso di lavoro chiamato Daily ETF Tracker. Cliccaci sopra.
Sulla destra, vedrai un pulsante con scritto "Run workflow". Cliccaci e conferma cliccando sul bottone verde.
Dopo qualche secondo, vedrai partire il job. Cliccaci sopra per vedere i log in tempo reale: se vedi il messaggio "Salvato con successo...", significa che l'orologio svizzero del cloud ha fatto il suo dovere!
Se vai ora nella tabella del tuo database su Supabase (Table Editor > prices), troverai i prezzi dei ticker presenti in `Anagrafica.csv`. Da questo momento in poi, lo script si eseguirà da solo ogni giorno alle 18:00 UTC, weekend inclusi.

## Passo 7: Pubblicare la pagina web con GitHub Pages

Il sito pubblica **solo `index.html`**, che contiene la dashboard. La vecchia pagina di prova è conservata nel repository come `index_old.html` e non viene distribuita. Il workflow `.github/workflows/pages.yml` copia `index.html` nell'artefatto Pages: nessun altro file del repository viene pubblicato.

1. Prima di pubblicare, controlla che `index.html` non contenga password, credenziali o dati che non vuoi rendere pubblici. La chiave Supabase publishable/anon è destinata all'uso nel browser: proteggi i dati con le policy RLS in sola lettura descritte sopra. Non inserire mai `DATABASE_URL` o una chiave Supabase service-role nella pagina. Il sito pubblicato è accessibile a chiunque abbia il link, anche se il repository è privato.
2. Apri il repository su GitHub e vai in **Settings** > **Pages**. In **Build and deployment**, imposta **Source** su **GitHub Actions**.
3. Fai commit e push sul branch `main`. Il workflow **Publish Dashboard to GitHub Pages** pubblicherà automaticamente la pagina; puoi anche avviarlo dalla scheda **Actions** con **Run workflow**.
4. Attendi il completamento del workflow. GitHub mostra l'indirizzo del sito nella pagina **Settings** > **Pages**; per un repository di progetto è normalmente `https://<nome-utente>.github.io/<nome-repository>/`.

Dopo ogni modifica a `index.html`, fai commit e push su `main` per aggiornare il sito. `index_old.html` resta nel repository come backup e non viene pubblicato. Se non trovi l'opzione GitHub Pages per il repository privato, controlla le possibilità offerte dal tuo piano GitHub.

## Esecuzione manuale sul PC

Apri PowerShell nella cartella del progetto e installa le dipendenze usando lo stesso interprete Python con cui eseguirai lo script:

```powershell
python -m pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env
python tracker.py
```

Nel file `.env`, sostituisci l'URI di esempio con la connection string del tuo database. Per usare EODHD imposta anche `EODHD_API_TOKEN` con il token del tuo account. Salva `.env` nella cartella del progetto: entrambi gli script lo caricano automaticamente e il file è escluso da Git. Non pubblicare né condividere questo file. Se `DATABASE_URL` o `EODHD_API_TOKEN` sono già definite nell'ambiente di Windows, hanno la precedenza sui valori in `.env`. Se `psycopg2` manca, installa le dipendenze con il comando indicato sopra. Il file `Anagrafica.csv` deve rimanere nella stessa cartella degli script.

Il job giornaliero legge da Yahoo Finance le quotazioni dell'ultimo mese e lo storico dei dividendi per i ticker con `Ticker_yf`; se `Ticker_yf` è vuoto, legge da EODHD le chiusure dell'ultimo mese tramite `Ticker_EODHD` (serve `EODHD_API_TOKEN`). Salva ogni seduta recente non ancora presente nel database usando la data effettiva fornita dalla sorgente; questa finestra consente di recuperare anche prezzi aggiornati settimanalmente o pubblicati in ritardo, senza assegnarli al giorno sbagliato. La quotazione odierna, se disponibile, viene aggiornata anche se esiste già; nei giorni senza nuove quotazioni non vengono creati record artificiali. Gli eventi dividendo Yahoo sono salvati tramite upsert su `(date, ticker)`, quindi riesecuzioni e correzioni dell'importo non creano duplicati.

### Backup del database Supabase

Per creare un backup logico del database, installa gli strumenti client PostgreSQL (con `pg_dump` della stessa versione principale del server o più recente). Usa in `DATABASE_URL` una connessione diretta o il pooler in modalità sessione. Se `pg_dump` non è nel `PATH`, puoi indicarne il percorso completo nel file `.env` del progetto:

```dotenv
PG_DUMP_PATH=C:\Program Files\PostgreSQL\17\bin\pg_dump.exe
```

Sostituisci `17` con la versione installata. In alternativa, puoi mettere un percorso relativo alla cartella del progetto, per esempio `tools\postgres\bin\pg_dump.exe`. La variabile d'ambiente `PG_DUMP_PATH`, se impostata, ha la precedenza sul valore nel file `.env`; se non viene impostata, lo script cerca `pg_dump` nel `PATH`. Poi esegui dalla cartella del progetto:

```powershell
python backup_supabase.py
```

Lo script legge `DATABASE_URL` e, facoltativamente, `PG_DUMP_PATH` dall'ambiente o da `.env` e salva un dump PostgreSQL in formato custom, con data e ora UTC nel nome, nella cartella `backups/` (creata automaticamente). Il dump include schema e dati di tutti gli schemi del database accessibili all'utente di connessione. I file possono contenere dati riservati: `backups/` è esclusa da Git, non condividerla e conserva una copia anche fuori dal PC. Il dump del database non include le impostazioni del progetto Supabase o i ruoli globali del server. Lo script segnala gli errori e non lascia un file di backup finale parziale.

### Caricamento iniziale dello storico

Per popolare `prices` con lo storico dei prezzi dal 1° gennaio 2022 e `dividends` con tutti gli eventi storici disponibili su Yahoo Finance per i ticker in `Anagrafica.csv`, esegui manualmente `backfill_history.py` dalla cartella del progetto:

```powershell
python backfill_history.py
```

Lo script scarica le chiusure e i dividendi da Yahoo Finance dal 1° gennaio 2022 incluso, usando `Ticker_yf`. Se questo campo è vuoto, scarica da EODHD le chiusure (`close`) tramite `Ticker_EODHD`, nei limiti del piano gratuito (massimo 365 giorni); per questa sorgente è necessario `EODHD_API_TOKEN` in `.env`. EODHD non viene usato per i dividendi. Lo script salva `Ticker` come identificativo canonico e sincronizza ticker e descrizioni del CSV in `instruments`. I dividendi Yahoo sono importi per azione alle date indicate, non una ricostruzione degli incassi del portafoglio. Se per un ticker non sono disponibili prezzi, lo script mostra un avviso e passa al ticker successivo. Prima di scrivere nel database verifica i dati raccolti; poi salva anagrafica, prezzi e dividendi disponibili in un'unica transazione. Per prezzi e dividendi inserisce solo le date mancanti e lascia invariati i record già presenti. Le chiavi univoche consentono di rilanciarlo senza creare duplicati. Richiede le stesse dipendenze e `DATABASE_URL` dello script giornaliero.

Per verificare che la variabile sia caricata puoi eseguire `$env:DATABASE_URL` in PowerShell; il valore salvato nel file locale viene letto all'avvio degli script.