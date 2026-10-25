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
```

La tabella `instruments` contiene il ticker canonico e la descrizione; `prices.ticker` lo usa come chiave esterna. Gli script mantengono `Anagrafica.csv` come fonte dell'anagrafica: usano `Ticker_yf` per interrogare Yahoo Finance e salvano `Ticker` in `prices`. `backfill_history.py` inizializza/aggiorna `instruments` insieme allo storico; il job giornaliero sincronizza le descrizioni prima di salvare i prezzi. Mantieni il CSV nella stessa cartella degli script e includilo nel repository, così sarà disponibile anche in GitHub Actions.

La pagina web visualizza i dati con la chiave pubblica Supabase: abilita RLS e consenti al ruolo `anon` solo la lettura delle due tabelle. Per un database nuovo, puoi applicare anche queste policy:

```sql
alter table public.instruments enable row level security;
alter table public.prices enable row level security;

grant select on public.instruments, public.prices to anon;

create policy "Allow anonymous read access to instruments"
    on public.instruments for select to anon using (true);
create policy "Allow anonymous read access to prices"
    on public.prices for select to anon using (true);
```

Non concedere permessi di scrittura al ruolo `anon`: le modifiche all'anagrafica vengono effettuate dagli script usando `DATABASE_URL`.

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

## Esecuzione manuale sul PC

Apri PowerShell nella cartella del progetto e installa le dipendenze usando lo stesso interprete Python con cui eseguirai lo script:

```powershell
python -m pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env
python tracker.py
```

Nel file `.env`, sostituisci l'URI di esempio con la connection string del tuo database. Salva `.env` nella cartella del progetto: entrambi gli script lo caricano automaticamente e il file è escluso da Git. Non pubblicare né condividere questo file. Se `DATABASE_URL` è già definita nell'ambiente di Windows, ha la precedenza sul valore in `.env`. Se `psycopg2` manca, installa le dipendenze con il comando indicato sopra. Il file `Anagrafica.csv` deve rimanere nella stessa cartella degli script.

Il job giornaliero verifica che Yahoo Finance abbia una quotazione con data odierna per ciascun ticker. Se non ci sono dati (per esempio nei giorni di borsa chiusa), quel ticker viene saltato e il prezzo della seduta precedente non viene registrato con la data corrente; gli altri ticker con dati odierni vengono salvati normalmente.

### Caricamento iniziale dello storico

Per popolare la tabella `prices` con lo storico disponibile su Yahoo Finance per tutti i ticker in `Anagrafica.csv`, esegui manualmente `backfill_history.py` dalla cartella del progetto:

```powershell
python backfill_history.py
```

Lo script scarica le quotazioni di chiusura da Yahoo Finance dal 1° gennaio 2022 in poi usando `Ticker_yf`, salva `Ticker` come identificativo e sincronizza ticker e descrizioni del CSV in `instruments`. Usa la data effettiva della seduta e aggiorna eventuali record con la stessa coppia data/ticker. Prima di scrivere nel database verifica che tutti i ticker abbiano dati; poi salva anagrafica e storico in un'unica transazione. Può quindi essere rilanciato senza creare righe duplicate. Richiede le stesse dipendenze e la stessa variabile `DATABASE_URL` dello script giornaliero.

Per verificare che la variabile sia caricata puoi eseguire `$env:DATABASE_URL` in PowerShell; il valore salvato nel file locale viene letto all'avvio degli script.