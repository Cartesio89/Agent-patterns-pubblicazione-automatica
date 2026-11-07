# Agent-patterns-pubblicazione-automatica

Scheletro Python di orchestratore e subagent per automazioni di personal branding.
Raccoglie notizie da feed RSS, prepara **bozze** per Instagram e per la newsletter, le fa
controllare e si ferma davanti all'approvazione umana.

Derivato dall'architettura di `gads_multiagent` (orchestratore, agenti, memoria, riflessione),
riscritto togliendo tutto ciò che riguarda Google Ads e correggendo i difetti elencati in
[docs/DIFETTI_CORRETTI.md](docs/DIFETTI_CORRETTI.md).

**Non pubblica niente.** Nel codice non c'è nessuna chiamata di pubblicazione. Pubblicare resta un
passo umano, o un modulo da aggiungere dopo.

## Stato di verifica (leggere prima di usarlo)

- **Provato (A, eseguito):** 26 test con un LLM finto, su Python 3.13. Coprono parsing JSON,
  cancello fail-closed, memoria thread-safe e atomica, cadenza, dipendenze tra agenti, controlli
  sulle bozze e integrazione con gli script del pacchetto Instagram italiano. Rompendo di proposito
  il cancello, il parsing e la scrittura atomica, i test falliscono.
- **NON provato:** nessuna chiamata a un modello vero, nessun feed vero scaricato dalla rete, nessuna
  prova su Windows, nessuna prova su Python 3.10-3.12 (il codice è scritto per 3.10+), nessuna prova
  della pianificazione con l'Utilità di pianificazione.
- **Il modello predefinito** (`claude-sonnet-5-5`) non è verificato sul tuo account. Se la prima
  chiamata fallisce con «model not found», imposta `ANTHROPIC_MODEL` nel `.env`.
- **Qualità dei testi:** dipende dal modello e dai prompt, che qui sono un punto di partenza. Il cancello
  di revisione è un secondo passaggio dello stesso tipo di modello: riduce gli errori, non li elimina.
  Resta la tua lettura.

## Struttura

```
branding_agents/
  core/        config, llm, memory, base_agent, orchestrator, reporter, util
  agents/      news_scout, instagram_draft, newsletter_draft (+ registro in __init__.py)
  main.py      un giro per esecuzione
tests/         26 test, nessuna rete
docs/          difetti corretti, diagramma animato (architettura.html)
```

Flusso: l'orchestratore calcola gli agenti in scadenza (cadenza sulla memoria) → NewsScout →
InstagramDraft e NewsletterDraft in parallelo → cancello di revisione (fail-closed) → sintesi → report
Markdown + JSON → **approvazione umana**. Apri `docs/architettura.html` nel browser per vederlo animato.

## Installazione (Windows, PowerShell)

```powershell
git clone https://github.com/Cartesio89/Agent-patterns-pubblicazione-automatica.git
cd Agent-patterns-pubblicazione-automatica
python -m pip install -r requirements.txt
copy env.example .env       # poi compila ANTHROPIC_API_KEY e FEEDS
python -m unittest discover -s tests -t . # i test non richiedono chiave né rete
python -m branding_agents --mode quick    # prima prova: solo raccolta notizie
```

Se `python` non risponde, prova `py`. Servono Python 3.10 o più recente e una chiave API Anthropic.
Report, memoria e log finiscono in `data/` (ignorata da git).

**Importante:** il file `.gitignore` serve a non pubblicare mai `.env` (con la chiave API) e `data/`. I file che iniziano con un punto non passano dal caricamento via browser di GitHub: se hai caricato i file dal browser, crea `.gitignore` a mano (Add file → Create new file) con queste righe:

```
.env
data/
__pycache__/
*.pyc
```

Modalità: `smart` (solo gli agenti in scadenza), `full` (tutti), `quick` (solo NewsScout).
Codici d'uscita: 0 ok, 1 configurazione, 3 almeno un agente in errore oppure bozze non approvate dalla revisione.

## Pianificazione

Non c'è un processo sempre acceso: ogni esecuzione fa un giro e termina. Su Windows, esempio con
l'Utilità di pianificazione (**sintassi non provata qui**, controllala con `schtasks /Create /?`):

```powershell
schtasks /Create /SC DAILY /ST 08:00 /TN "AgentiBranding" /TR "cmd /c cd /d C:\percorso\Agent-patterns-pubblicazione-automatica && python -m branding_agents --mode smart"
```

## Pacchetto Instagram italiano (opzionale)

Se imposti `IG_PACK_PATH` alla cartella `skills\` del pacchetto
[Automazione-pubblicazione-post](https://github.com/Cartesio89/Automazione-pubblicazione-post),
`InstagramDraft` passa ogni didascalia da `humanize.py` (pulizia) e `caption.py` (controllo) e riporta il
verdetto in `qa`. Le soglie di quegli script non sono calibrate su dati italiani reali.

## Aggiungere un agente

1. Crea `branding_agents/agents/mio_agente.py` con una classe che estende `BaseAgent`
   (`_fetch_data`, `_build_prompt`, opzionalmente `_postprocess` per i controlli nel codice).
2. Metti nel prompt di sistema la prima riga `[AGENT:mio_agente]`: i test finti lo usano per instradare.
3. Registralo in `agents/__init__.py` e, se serve, imposta `depends_on` (deve riuscire) o `after` (solo ordine).
4. Imposta la cadenza con `CADENCE_MIO_AGENTE` nel `.env`, oppure in `min_days_between`.

Regola del progetto: i controlli che contano (limiti, link, numeri) vanno nel **codice**, non nel prompt.

## Cosa manca per pubblicare davvero

Moduli di pubblicazione (per esempio Instagram Graph API, bozze TikTok) non ci sono e non sono provati.
Vanno aggiunti separatamente, dietro l'approvazione umana, e richiedono app e permessi della piattaforma.
