---
name: agent-patterns-branding
description: Scheletro Python di orchestratore e subagent per automazioni di contenuti (RSS, bozze Instagram e newsletter, cancello di revisione, approvazione umana). Usalo per costruire o estendere agenti multi-agente in qualsiasi progetto.
---

# agent-patterns-branding

Questa skill NON contiene il codice: dice dove sta e come usarlo. Il codice è nella repo pubblica
`Cartesio89/Agent-patterns-pubblicazione-automatica` (Python 3.10+, solo `anthropic`, `defusedxml`,
`python-dotenv`). È l'architettura di `gads_multiagent` (orchestratore, agenti, memoria, riflessione)
riscritta senza Google Ads e con i difetti corretti.

## 1. Trova il codice, prima di tutto

Controlla in quest'ordine e fermati al primo che esiste:

1. Una copia locale: cerca `**/branding_agents/core/orchestrator.py` con Glob. Se c'è, usala.
2. Clonala in una cartella nuova e vuota:
   `git clone https://github.com/Cartesio89/Agent-patterns-pubblicazione-automatica.git`
3. Se non hai git o la rete: leggi i file con il connettore GitHub, oppure da
   `https://raw.githubusercontent.com/Cartesio89/Agent-patterns-pubblicazione-automatica/main/<percorso>`.
4. Se niente funziona, **dillo subito** e chiedi all'utente lo zip o i file. Non ricostruire il codice a memoria.

Prima di modificare qualsiasi cosa leggi `README.md` e `docs/DIFETTI_CORRETTI.md` della repo.

## 2. Le regole di progetto (non vanno rotte)

1. **Il codice non pubblica.** Gli agenti producono bozze con uno stato; pubblicare è un passo umano.
   Aggiungi moduli di pubblicazione solo se l'utente lo chiede, e dietro approvazione umana esplicita.
2. **Cancello fail-closed.** Una bozza è approvata solo con `approved: true` esplicito. Se la revisione
   fallisce, risponde senza la chiave o con un valore diverso da `True`, non c'è approvazione.
3. **Un JSON rotto è un errore**, mai un risultato vuoto "riuscito". Un agente dipendente da una fonte
   fallita viene saltato.
4. **I controlli che contano stanno nel codice, non nel prompt:** limiti di lunghezza, numero di hashtag,
   link che devono comparire nella fonte, segnaposto rimasti. Un controllo del codice non si scavalca
   con l'approvazione dell'LLM.
5. **La cadenza la calcola il codice** dalla memoria, contando solo le esecuzioni riuscite. Niente piano
   deciso dall'LLM.
6. **Il modello non è scritto nel codice:** `ANTHROPIC_MODEL` da ambiente, `LLM_TEMPERATURE=none` se
   l'API rifiuta il parametro.
7. **Memoria atomica:** scrittura su file temporaneo + `os.replace`; un file corrotto si mette da parte, non si sovrascrive.
8. **Un giro per esecuzione:** la pianificazione è del sistema operativo (su Windows l'Utilità di
   pianificazione), non di un processo Python sempre acceso.

## 3. Come si estende

- Nuovo agente: modulo in `branding_agents/agents/` con classe che estende `BaseAgent` (`_fetch_data`,
  `_build_prompt`, `_postprocess` per i controlli nel codice); prima riga del prompt di sistema
  `[AGENT:nome]` (i test finti instradano da lì); registralo in `agents/__init__.py`;
  `depends_on` = deve riuscire, `after` = solo ordine.
- Per usare l'architettura in un altro progetto copia `branding_agents/core/` com'è e cambia solo
  `agents/`. Non ricopiare i difetti dell'originale.
- Test: `python -m unittest discover -s tests -t .` (nessuna rete, nessuna chiave). Dopo ogni modifica
  alla logica del cancello, del parsing o della memoria, aggiungi o aggiorna il test relativo.
- Pacchetto Instagram italiano (opzionale): `IG_PACK_PATH` alla cartella `skills/` di
  `Cartesio89/Automazione-pubblicazione-post`; vedi la skill `instagram-it-pack`.

## 4. Cosa dire sempre sullo stato di verifica

- Provato: 26 test con LLM finto, su Python 3.13.
- NON provato: nessuna chiamata a un modello vero, nessun feed scaricato dalla rete, nessuna prova su
  Windows né su Python 3.10-3.12, nessuna prova della pianificazione. L'id del modello predefinito
  non è verificato sull'account dell'utente.
- Se non puoi eseguire il codice in questa sessione, dillo prima di procedere e non presentare come
  risultati quelli che sono solo letture del codice.
