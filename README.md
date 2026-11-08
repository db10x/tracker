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
    isin text,
    description text,
    tipo text,
    comparto text,
    ticker_yf text,
    ticker_eodhd text,
    isin_stfi text,
    ticker_inv text
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

La tabella `instruments` contiene tutte le informazioni dell'anagrafica; `prices.ticker`, `dividends.ticker` e `portfolio_positions.ticker` lo usano come chiave esterna. `dividends.amount` memorizza il dividendo per azione da Yahoo Finance o, per i bond SimpleTools, l'importo lordo per 100 di nominale. L'anagrafica è gestita direttamente in `public.instruments`; `tracker.py` e `backfill_history.py` leggono i dati dal database e non usano il CSV. Le fonti prezzi sono selezionate in questo ordine: Yahoo Finance (`Ticker_yf`), EODHD (`Ticker_EODHD`) e SimpleTools (`ISIN_stfi`). Yahoo Finance fornisce i dividendi; SimpleTools fornisce le cedole dei bond quando entrambi i ticker sono vuoti.

L'anagrafica iniziale è già stata caricata in `public.instruments`. Per un database nuovo, crea `instruments` con il DDL sopra e inserisci i record direttamente in Supabase. Gli script ordinari leggono l'anagrafica da questa tabella e non usano `Anagrafica.csv`.


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

Per registrare acquisti, vendite, incassi e movimenti di liquidità, esegui anche questo SQL dopo aver creato `portfolios`, `instruments` e `portfolio_positions`. Ogni movimento è associato a un broker; `source` indica invece come è stato acquisito il dato (ad esempio inserimento manuale o importazione):

```sql
create table public.brokers (
    id integer generated always as identity primary key,
    name text not null unique check (btrim(name) <> '')
);

create table public.portfolio_transactions (
    id bigint generated always as identity primary key,
    portfolio_id integer not null
        references public.portfolios (id),
    broker_id integer not null
        references public.brokers (id),
    ticker text
        references public.instruments (ticker),
    transaction_type text not null check (transaction_type in (
        'BUY', 'SELL', 'DIVIDEND',
            'FEE', 'TAX', 'DEPOSIT', 'WITHDRAWAL',
            'TRANSFER_OUT', 'TRANSFER_IN'
        )),
    transaction_date date not null,
    quantity numeric(20, 8) check (quantity > 0),
    unit_price numeric(20, 8) check (unit_price > 0),
    gross_amount numeric(20, 8) not null check (gross_amount >= 0),
    fees numeric(20, 8) not null default 0 check (fees >= 0),
    taxes numeric(20, 8) not null default 0 check (taxes >= 0),
    net_cash_amount numeric(20, 8),
    source text,
    notes text,
    check (
        transaction_type not in (
            'BUY', 'SELL', 'DIVIDEND', 'TRANSFER_OUT', 'TRANSFER_IN'
        )
        or ticker is not null
    ),
    check (
        transaction_type not in ('BUY', 'SELL')
        or (quantity is not null and unit_price is not null)
    ),
    check (
        transaction_type not in ('TRANSFER_OUT', 'TRANSFER_IN')
        or quantity is not null
    ),
    check (
        transaction_type not in ('DEPOSIT', 'WITHDRAWAL')
        or ticker is null
    ),
    check (
        (
            transaction_type in ('TRANSFER_OUT', 'TRANSFER_IN')
            and net_cash_amount is null
        )
        or (
            transaction_type not in ('TRANSFER_OUT', 'TRANSFER_IN')
            and net_cash_amount is not null
        )
    )
);

create index portfolio_transactions_portfolio_date_idx
    on public.portfolio_transactions (portfolio_id, transaction_date);
create index portfolio_transactions_broker_date_idx
    on public.portfolio_transactions (broker_id, transaction_date);
create index portfolio_transactions_ticker_date_idx
    on public.portfolio_transactions (ticker, transaction_date);

alter table public.brokers enable row level security;
alter table public.portfolio_transactions enable row level security;
revoke all on public.brokers, public.portfolio_transactions from anon, authenticated;
```

`portfolio_transactions.id` è una chiave tecnica generata automaticamente per identificare senza ambiguità ogni riga. Tutti gli importi sono in euro; `transaction_date` è l'unica data. `external_id` e `created_at` non sono previsti. `ticker` è obbligatorio per acquisti, vendite, dividendi e trasferimenti; il tipo `DIVIDEND` comprende sia i dividendi azionari sia le cedole obbligazionarie, distinguibili tramite `instruments.tipo`. Il ticker può essere nullo per versamenti/prelievi e per commissioni o imposte riferite all'intero conto. `quantity` e `unit_price` sono obbligatori per acquisti e vendite; per i trasferimenti è obbligatoria solo `quantity`. Tutti i valori presenti di quantità e prezzo unitario devono essere positivi. `gross_amount`, `fees` e `taxes` sono valori non negativi; per un trasferimento, `gross_amount` rappresenta il valore dei titoli trasferiti, mentre `net_cash_amount` è nullo perché non c'è un movimento di liquidità. Per gli altri tipi `net_cash_amount` è obbligatorio e indica il movimento netto effettivo, positivo per un accredito sul conto titoli e negativo per un addebito. Commissioni e imposte sono dettagli già inclusi nell'importo netto, non vanno sommate di nuovo.

Per una singola operazione d'acquisto, vendita o dividendo/cedola, registra in `net_cash_amount` l'importo effettivamente addebitato o accreditato, incluse commissioni e ritenute. Versamenti (`DEPOSIT`) sono accrediti al conto e quindi positivi; prelievi (`WITHDRAWAL`) sono addebiti e quindi negativi. Per trasferire titoli tra due broker dello stesso portafoglio, registra due righe con stessa data, ticker e quantità: `TRANSFER_OUT` sul broker di origine e `TRANSFER_IN` su quello di destinazione. Entrambi hanno `net_cash_amount` nullo e non modificano la liquidità; eventuali commissioni di trasferimento vanno registrate separatamente come `FEE`. I tipi sono in maiuscolo come indicato dal vincolo. `source` descrive la provenienza del dato e non il broker: per esempio `manual` o `statement_import`. La pagina pubblica legge in sola lettura `brokers` e `portfolio_transactions` tramite il ruolo `anon`; i nomi degli intermediari e tutti i movimenti, gli importi e le note dei portafogli sono quindi visibili a chiunque acceda alla pagina. Non concedere permessi di scrittura. Le istruzioni RLS per abilitare questa lettura sono riportate sotto; se i dati non devono essere pubblici, non applicarle e usa un backend o un'autenticazione con policy per utente.

Per il TIR del portafoglio, i versamenti e i prelievi vanno interpretati dal punto di vista dell'investitore (versamento negativo, prelievo positivo); la valutazione finale deve includere sia i titoli sia la liquidità residua. Acquisti, vendite, dividendi/cedole, commissioni, imposte e trasferimenti tra broker sono movimenti interni al portafoglio e non vanno conteggiati una seconda volta come flussi esterni. `net_cash_amount` consente di ricostruire la liquidità cumulativa dai movimenti registrati.

Per gli strumenti con `instruments.tipo = 'Bond'`, la pagina calcola il valore di mercato e gli importi di P/L in euro con la formula `prezzo × quantità ÷ 100`, poiché la quotazione è espressa per 100 di valore nominale. Per gli altri strumenti usa `prezzo × quantità`. Ad esempio, un bond quotato 100 con quantità 2000 vale 2000 €.

La tabella delle posizioni può essere filtrata per `Tipo` e `Comparto` anche nella vista compatta, usando i menu allineati a sinistra nella barra (senza il titolo `Posizioni`) e il pulsante `Espandi tabella` a destra; i filtri non aggiungono colonne alla vista compatta. Nella vista espansa le colonne `Tipo` e `Comparto` compaiono dopo `Ticker` e i relativi menu filtro sono nelle intestazioni. I menu sono sincronizzati e consentono di combinare i due criteri; per tornare a tutti i risultati seleziona `Tutti i tipi` e `Tutti i comparti`.

Nella pagina, il menu consente di scegliere solo un portafoglio: all'avvio viene selezionato il primo in ordine alfabetico e vengono caricati solo i suoi prezzi e le sue posizioni. Il menu laterale, comprimibile e con stato memorizzato nel browser, alterna la panoramica con grafico/posizioni e la vista Transazioni, che mostra i movimenti registrati, le quantità nette ricostruite per intermediario da acquisti/vendite/trasferimenti e il cash calcolato dai flussi `net_cash_amount`, dettagliato per intermediario con totale complessivo. Le colonne delle tabelle transazioni e titoli sono ordinabili. Se non ci sono portafogli, la pagina chiede di crearne uno invece di mostrare tutti i ticker.

La pagina web visualizza i dati con la chiave pubblica Supabase. Abilita RLS e consenti al ruolo `anon` solo la lettura delle tabelle usate dalla pagina: `instruments`, `prices`, `portfolios`, `portfolio_positions`, `dividends`, `brokers` e `portfolio_transactions`. Sono pubblicamente leggibili i nomi dei portafogli, le posizioni, i prezzi medi di acquisto, i dividendi e l'intero storico delle transazioni, inclusi importi e note. Applica le policy sulle transazioni solo se accetti questa esposizione pubblica. La pagina non concede né richiede permessi di scrittura. Mostra la variazione percentuale rispetto al prezzo medio di acquisto e, per i dividendi, un marcatore `D` sul grafico con tooltip sull'importo; per i bond SimpleTools, l'importo rappresenta la cedola lorda per 100 di nominale e gli eventi futuri sono esclusi. Per un database nuovo, applica:

```sql
alter table public.instruments enable row level security;
alter table public.prices enable row level security;
alter table public.portfolios enable row level security;
alter table public.portfolio_positions enable row level security;
alter table public.dividends enable row level security;
alter table public.brokers enable row level security;
alter table public.portfolio_transactions enable row level security;

grant select on public.instruments, public.prices, public.portfolios, public.portfolio_positions, public.dividends to anon;
grant select on public.brokers, public.portfolio_transactions to anon;

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
create policy "Allow anonymous read access to brokers"
    on public.brokers for select to anon using (true);
create policy "Allow anonymous read access to portfolio transactions"
    on public.portfolio_transactions for select to anon using (true);
```

Le righe di `portfolio_positions` devono riferirsi a ticker già presenti in `instruments`. Dopo l'importazione iniziale, modifica l'anagrafica direttamente in Supabase con un ruolo autorizzato; crea o modifica portafogli e posizioni allo stesso modo, non dalla chiave anon della pagina.

Se hai già applicato le policy anon per `instruments` e `prices`, non rieseguire quelle istruzioni: applica solo le seguenti per le nuove tabelle.

```sql
alter table public.portfolios enable row level security;
alter table public.portfolio_positions enable row level security;
alter table public.dividends enable row level security;
alter table public.brokers enable row level security;
alter table public.portfolio_transactions enable row level security;

grant select on public.portfolios, public.portfolio_positions, public.dividends to anon;
grant select on public.brokers, public.portfolio_transactions to anon;

create policy "Allow anonymous read access to portfolios"
    on public.portfolios for select to anon using (true);
create policy "Allow anonymous read access to portfolio positions"
    on public.portfolio_positions for select to anon using (true);
create policy "Allow anonymous read access to dividends"
    on public.dividends for select to anon using (true);
create policy "Allow anonymous read access to brokers"
    on public.brokers for select to anon using (true);
create policy "Allow anonymous read access to portfolio transactions"
    on public.portfolio_transactions for select to anon using (true);
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


Se usi EODHD, crea anche il secret `EODHD_API_TOKEN` e inserisci il token API del tuo account. È necessario solo per le righe con `Ticker_yf` vuoto e `Ticker_EODHD` valorizzato. Se entrambi i ticker sono vuoti, lo script usa SimpleTools: compila `ISIN_stfi` con l'ISIN riconosciuto dal sito.


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
Dopo qualche secondo, vedrai partire il job. Cliccaci sopra per vedere i log in tempo reale: se vedi il messaggio "Salvato con successo...", significa che l'orologio svizzero del cloud ha fatto il suo dovere! I ticker elaborati provengono da Supabase e lo script si eseguirà ogni giorno alle 18:00 UTC, weekend inclusi.

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

Nel file `.env`, sostituisci l'URI di esempio con la connection string del tuo database. Per usare EODHD imposta anche `EODHD_API_TOKEN` con il token del tuo account. Salva `.env` nella cartella del progetto: gli script lo caricano automaticamente e il file è escluso da Git. Non pubblicare né condividere questo file. Se `DATABASE_URL` o `EODHD_API_TOKEN` sono già definite nell'ambiente di Windows, hanno la precedenza sui valori in `.env`. Se `psycopg2` manca, installa le dipendenze con il comando indicato sopra. Gli strumenti vengono letti dalla tabella `public.instruments` di Supabase.

### Importazione movimenti DEGIRO

`import_degiro_account.py` importa `import/Degiro Account.csv` in `public.portfolio_transactions`, usando `Anagrafica.csv` per associare gli ISIN ai ticker. Richiede che esistano le tabelle `portfolio_transactions`, `portfolios`, `brokers` e `instruments`, che in `public.brokers` esista il broker con nome esatto `Degiro` e che `DATABASE_URL` punti a una connessione autorizzata alla scrittura. Il suo ID viene letto dal database; l'ID del portafoglio resta invece un parametro necessario perché `brokers` non contiene un'associazione a un portafoglio.

Lo script richiede l'ID del portafoglio. La prima esecuzione mostra solo un riepilogo; per esempio, inserisci l'ID corretto e verifica l'anteprima prima di aggiungere `--apply`:

```powershell
$portfolioId = Read-Host "ID del portafoglio"
python import_degiro_account.py --portfolio-id $portfolioId
python import_degiro_account.py --portfolio-id $portfolioId --apply
```

Le commissioni abbinate all'acquisto tramite `ID Ordine` sono incluse nel campo `fees` del BUY e nel relativo `net_cash_amount`; più eseguiti dello stesso ordine sono aggregati. Depositi flatex sono registrati come `DEPOSIT`. Costi di connessione e interessi non nulli sono registrati come `FEE`; `DEGIRO courtesy` è anch'esso un `FEE`, con importo netto positivo per compensare gli addebiti. Le uscite titoli a variazione zero sono registrate come `TRANSFER_OUT` con quantità e valore ricavati dalla descrizione. I cash sweep e le altre righe con variazione zero/vuota sono ignorati. I riferimenti alle righe sorgenti salvati in `notes` rendono le riesecuzioni idempotenti; se una riga già importata cambia, lo script interrompe l'operazione invece di duplicarla.

Le righe con tipologia non riconosciuta vengono elencate nell'output con numero di riga, data, variazione e descrizione e non vengono importate; le righe valide continuano a essere elaborate. Le righe `CAMBIO DI PRODOTTO` e le variazioni zero/vuote delle tipologie riconosciute sono ignorate senza avviso; lo stesso vale per le righe di trasferimento della liquidità.

Il job giornaliero legge da Yahoo Finance le quotazioni dell'ultimo mese e lo storico dei dividendi per i ticker con `Ticker_yf`; se è vuoto, legge da EODHD tramite `Ticker_EODHD` (serve `EODHD_API_TOKEN`) o, se anche questo è vuoto, da SimpleTools tramite `ISIN_stfi`. Per gli strumenti `Bond` SimpleTools importa anche le cedole del piano del titolo dal 1° gennaio 2022 a oggi, ignorando le date future e normalizzando gli importi per 100 di nominale. Salva ogni seduta recente non ancora presente nel database usando la data effettiva fornita dalla sorgente; questa finestra consente di recuperare anche prezzi aggiornati settimanalmente o pubblicati in ritardo, senza assegnarli al giorno sbagliato. La quotazione odierna, se disponibile, viene aggiornata anche se esiste già; nei giorni senza nuove quotazioni non vengono creati record artificiali. Dividendi Yahoo e cedole SimpleTools sono salvati tramite upsert su `(date, ticker)`, quindi riesecuzioni non creano duplicati.

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

Puoi popolare `prices` con lo storico dei prezzi dal 1° gennaio 2022 e `dividends` con tutti gli eventi storici disponibili su Yahoo Finance. Esegui manualmente `backfill_history.py` dalla cartella del progetto:

```powershell
python backfill_history.py
```

Lo script legge gli strumenti da `public.instruments` e scarica le chiusure e i dividendi da Yahoo Finance dal 1° gennaio 2022 incluso, usando `ticker_yf`. Se è vuoto, usa EODHD con `ticker_eodhd` (massimo 365 giorni; richiede `EODHD_API_TOKEN` in `.env`); se sono vuoti entrambi, usa SimpleTools con `isin_stfi` e lo storico disponibile sul sito. Per gli strumenti `Bond`, SimpleTools ricava anche le cedole dal piano del titolo, salvando soltanto le date dal 1° gennaio 2022 a oggi e ignorando quelle future; gli importi sono normalizzati per 100 di nominale. `ticker` è l'identificativo canonico usato nel database. I dividendi Yahoo sono importi per azione alle date indicate. Se per un ticker non sono disponibili prezzi, lo script mostra un avviso e passa al ticker successivo. I prezzi e i dividendi disponibili vengono salvati in un'unica transazione. Per prezzi e dividendi inserisce solo le date mancanti e lascia invariati i record già presenti. Le chiavi univoche consentono di rilanciarlo senza creare duplicati. Richiede le stesse dipendenze e `DATABASE_URL` dello script giornaliero.

Per verificare che la variabile sia caricata puoi eseguire `$env:DATABASE_URL` in PowerShell; il valore salvato nel file locale viene letto all'avvio degli script.