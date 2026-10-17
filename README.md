# tracker
script giornaliero per scaricare i prezzi dei titoli su DB (Supabase)


## Passo 1: Creare il database su Supabase 
Supabase ti offre un database PostgreSQL nel cloud in pochi secondi.
Vai su supabase.com e registrati (puoi usare direttamente il tuo account GitHub).
Clicca su "New Project".
Scegli un nome per il progetto (es. etf-tracker), imposta una password sicura per il database e seleziona una regione vicina (es. Frankfurt o EU West). Clicca su "Create new project" (ci vorrà circa un minuto per l'attivazione).
Una volta dentro la dashboard, vai sull'icona del database (SQL Editor nel menu a sinistra) e clicca su "New Query".
Incolla questo codice SQL per creare la tabella che conterrà i tuoi dati e premi "Run":

SQL
create table etf_prices (
    id serial primary key,
    date date not null unique,
    ticker text not null,
    price numeric(10, 2) not null,
    created_at timestamp with time zone default timezone('utc'::text, now()) not null
);

Ora recuperiamo le credenziali: vai nelle Impostazioni del progetto (icona ingranaggio a sinistra) > Database. Scorri fino alla sezione Connection string e seleziona la tab URI. Copia la stringa (sarà simile a postgresql://postgres:[YOUR-PASSWORD]@db.xxxx.supabase.co:5432/postgres). Conserva questa stringa, ti servirà tra poco.

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
In Secret incolla la stringa di connessione a Supabase c (ricordati di sostituire [YOUR-PASSWORD] con la vera password che hai scelto quando hai creato il progetto su Supabase).
Clicca "Add secret".

Attenzione
Special symbols in passwords#
If you use special symbols in your Postgres password, you must remember to [percent-encode](https://en.wikipedia.org/wiki/Percent-encoding) your password later if using the Postgres connection string, for example, postgresql://postgres.projectref:p%3Dword@aws-0-us-east-1.pooler.supabase.com:6543/postgres

## Passo 5: Creare l'automazione (GitHub Action)
Ora diciamo a GitHub quando far girare lo script. Creeremo una cartella e un file di configurazione.
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
Se vai ora nella tabella del tuo database su Supabase (Table Editor > etf_prices), troverai la riga con il prezzo del SWDA. Da questo momento in poi, lo script si eseguirà da solo ogni giorno feriale alla sera.
