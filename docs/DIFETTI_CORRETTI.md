# Difetti di gads_multiagent e cosa è stato fatto

Tutti i difetti sono stati trovati **leggendo** il codice originale (non eseguendolo, salvo dove indicato).
La colonna "Verifica" dice che prova c'è, senza arrotondare.

| # | Difetto nell'originale | Verifica | Correzione | Test |
| --- | --- | --- | --- | --- |
| 1 | `ask_json` restituiva `{"error": ...}` se il JSON era rotto, e l'agente dichiarava comunque «Analisi completata» con dati vuoti | letto nel codice | `LLMParseError` dopo un tentativo di riparazione; l'agente risulta `error`; gli agenti dipendenti vengono saltati | `test_ask_json_raises_after_retries_and_counts_calls`, `test_llm_garbage_makes_agent_error_not_ok`, `test_missing_required_key_is_error` |
| 2 | La riflessione approvava se mancava la chiave `approved` (`reflection.get("approved", True)`) | letto nel codice | Fail-closed: conta solo `approved: true` esplicito; se la revisione fallisce, nessuna bozza è approvata; i problemi trovati dal codice non si scavalcano | `test_reflection_is_fail_closed` (4 casi), `test_deterministic_problems_override_llm_approval`. Rompendo di proposito il codice, i casi `no_key` e `truthy_string` falliscono |
| 3 | Scritture concorrenti su `memory.json` senza lock | **race condition non riprodotta**: 12 prove, 8 thread, 150 scritture ciascuno, nessun file corrotto e nessuna voce persa | Lock comunque, per prudenza | `test_concurrent_writes_keep_every_entry_and_valid_file` |
| 3b | Scrittura non atomica; un file corrotto veniva scartato in silenzio e sovrascritto al salvataggio successivo | letto nel codice, non provato | Scrittura atomica (file temporaneo + `os.replace`); il file corrotto viene rinominato con data e conservato | `test_failed_save_leaves_previous_file_intact` (fallisce se si torna a `write_text`), `test_corrupt_file_is_quarantined_not_overwritten` |
| 4 | Modello fisso nel codice (`claude-sonnet-4-20250514`) | letto nel codice | `ANTHROPIC_MODEL` da ambiente; `LLM_TEMPERATURE=none` per non inviare il parametro | `test_config_model_from_env`, `test_sdk_error_becomes_llmerror_and_temperature_optional`. **L'id predefinito (`claude-sonnet-5-5`) non è verificato sul tuo account.** |
| 5 | Piano «smart» deciso da una chiamata LLM | letto nel codice | Cadenza calcolata dal codice sulla memoria, solo esecuzioni riuscite; tolleranza di ~2,4 ore | `test_smart_plan_follows_cadence_and_adds_dependencies`, `test_cadence_counts_only_successful_runs` |
| 6 | `schedule` richiede un processo Python sempre acceso | letto nel codice | Un giro per esecuzione; pianificazione affidata al sistema operativo | non testabile qui, vedi README |
| 7 | `config.py` creava cartelle all'import | letto nel codice | Nessun effetto collaterale all'import | — |
| 8 | `traq_bridge` richiamava `traq_av_automation.py`, assente dalla cartella | verificato: il file non c'è | Non portato | — |

## Difetti trovati durante i test del nuovo codice

- `caption.py` del pacchetto Instagram esce con codice 1 quando il verdetto non è «PRONTA»: il wrapper lo trattava come guasto. Corretto: conta il JSON prodotto. Test: `test_nonzero_exit_with_valid_json_is_not_a_failure`.
- La pulizia dell'HTML nei feed lasciava doppi spazi. Corretto.
