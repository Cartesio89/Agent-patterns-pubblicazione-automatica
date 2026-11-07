"""Test con un LLM finto: nessuna chiamata di rete, nessuna chiave API.

Verificano il comportamento della logica (parsing, cancello fail-closed, memoria, cadenza,
dipendenze). NON verificano la qualità dei testi generati da un LLM vero.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from branding_agents.agents import REGISTRY
from branding_agents.agents.instagram_draft import validate_ig_draft
from branding_agents.agents.news_scout import NewsScout, parse_feed
from branding_agents.core.base_agent import STATUS_ERROR, STATUS_OK, STATUS_SKIPPED
from branding_agents.core.config import DEFAULT_MODEL, LLMConfig, SystemConfig
from branding_agents.core.llm import LLMClient, LLMError, LLMParseError, extract_json
from branding_agents.core.memory import Memory, MemoryEntry
from branding_agents.core.orchestrator import Orchestrator
from branding_agents.core.reporter import render_markdown

RSS = """<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>Nuovo modello open</title><link>https://ex.com/a</link><description>&lt;p&gt;Testo &lt;b&gt;uno&lt;/b&gt;&lt;/p&gt;</description></item>
<item><title>Senza link</title><link></link></item>
<item><title>Link strano</title><link>javascript:alert(1)</link></item>
<item><title>Altra notizia</title><link>https://ex.com/b</link><description>Testo due</description></item>
</channel></rss>"""
ATOM = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>Voce atom</title><link rel="alternate" href="https://ex.com/atom1"/><summary>Riassunto</summary><updated>2026-11-01</updated></entry>
</feed>"""


class FakeLLM:
    """Risponde in base al tag [AGENT:...] nel prompt di sistema. `script` mappa tag -> dict | Exception | callable."""

    def __init__(self, script):
        self.script, self.calls = script, []
        self._lock = threading.Lock()

    def ask_json(self, system, user, temperature=None):
        tag = system.split("]")[0].split(":")[1]
        with self._lock:
            self.calls.append(tag)
        out = self.script[tag]
        out = out(user) if callable(out) else out
        if isinstance(out, Exception):
            raise out
        return out


def good_script(link="https://ex.com/a"):
    return {
        "news_scout": {"summary": "scelte", "items": [{"title": "x", "link": link, "why": "perché"},
                                                    {"title": "inventata", "link": "https://falso.example/x", "why": "no"}]},
        "instagram_draft": {"summary": "bozza", "drafts": [{
            "format": "post", "hook": "Un hook concreto", "caption": "Testo del post. #ia #tech",
            "source_link": link, "claims_to_verify": ["la data"]}]},
        "newsletter_draft": {"summary": "nl", "drafts": [{
            "subject": "Oggetto", "preview_text": "p", "body_markdown": "corpo", "links": [link]}]},
        "reviewer": {"approved": True, "confidence": 0.9, "issues": [], "blocked_indexes": []},
        "synthesizer": {"executive_summary": "Tutto ok."},
    }


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.mem = Memory(self.tmp / "m.json", max_entries=1000)
        self.cfg = SystemConfig(feeds=["https://feed.example/rss"])
        self._orig = NewsScout.fetcher
        NewsScout.fetcher = staticmethod(lambda url, timeout=15: RSS)

    def tearDown(self):
        NewsScout.fetcher = self._orig

    def orch(self, script):
        return Orchestrator(FakeLLM(script), self.mem, self.cfg)


class TestParsing(unittest.TestCase):
    def test_extract_json_variants(self):
        self.assertEqual(extract_json('{"a": 1}'), {"a": 1})
        self.assertEqual(extract_json('```json\n{"a": 1}\n```'), {"a": 1})
        self.assertEqual(extract_json('Ecco: {"a": 1} fine'), {"a": 1})
        self.assertIsNone(extract_json("niente json"))
        self.assertIsNone(extract_json("[1, 2]"))
        self.assertIsNone(extract_json(""))

    def test_ask_json_raises_after_retries_and_counts_calls(self):
        calls = []

        def create(**kw):
            calls.append(kw)
            return SimpleNamespace(content=[SimpleNamespace(type="text", text="non è json")])

        client = SimpleNamespace(messages=SimpleNamespace(create=create))
        llm = LLMClient(LLMConfig(api_key="k", json_retries=2), client)
        with self.assertRaises(LLMParseError):
            llm.ask_json("s", "u")
        self.assertEqual(len(calls), 3)

    def test_ask_json_recovers_on_retry(self):
        replies = iter(["rotto", '{"ok": true}'])
        client = SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: SimpleNamespace(
            content=[SimpleNamespace(type="text", text=next(replies))])))
        self.assertEqual(LLMClient(LLMConfig(api_key="k"), client).ask_json("s", "u"), {"ok": True})

    def test_sdk_error_becomes_llmerror_and_temperature_optional(self):
        seen = {}

        def boom(**kw):
            seen.update(kw)
            raise RuntimeError("rete giù")

        client = SimpleNamespace(messages=SimpleNamespace(create=boom))
        with self.assertRaises(LLMError):
            LLMClient(LLMConfig(api_key="k", temperature=None), client).ask("s", "u")
        self.assertNotIn("temperature", seen)

    def test_feed_parsing(self):
        items = parse_feed(RSS)
        self.assertEqual([i["link"] for i in items], ["https://ex.com/a", "https://ex.com/b"])
        self.assertEqual(items[0]["snippet"], "Testo uno")
        atom = parse_feed(ATOM)
        self.assertEqual(atom[0]["link"], "https://ex.com/atom1")

    def test_config_model_from_env(self):
        old = {k: os.environ.get(k) for k in ("ANTHROPIC_MODEL", "LLM_TEMPERATURE")}
        try:
            os.environ.pop("ANTHROPIC_MODEL", None)
            self.assertEqual(LLMConfig.from_env().model, DEFAULT_MODEL)
            os.environ["ANTHROPIC_MODEL"] = "modello-x"
            os.environ["LLM_TEMPERATURE"] = "none"
            cfg = LLMConfig.from_env()
            self.assertEqual((cfg.model, cfg.temperature), ("modello-x", None))
        finally:
            for k, v in old.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)

    def test_draft_validator(self):
        base = {"hook": "h", "caption": "c #a #b #c #d #e #f", "source_link": "https://ex.com/a"}
        probs = validate_ig_draft(base, {"https://ex.com/a"})
        self.assertTrue(any("6 hashtag" in p for p in probs))
        self.assertTrue(any("source_link" in p for p in validate_ig_draft(base, {"https://altro"})))
        self.assertTrue(any("segnaposto" in p for p in validate_ig_draft({**base, "caption": "x {{da verificare}}"}, {"https://ex.com/a"})))
        self.assertTrue(any("2200" in p for p in validate_ig_draft({**base, "caption": "x" * 2300}, {"https://ex.com/a"})))
        self.assertEqual(validate_ig_draft({**base, "caption": "ok #a"}, {"https://ex.com/a"}), [])


class TestPackTool(unittest.TestCase):
    def test_nonzero_exit_with_valid_json_is_not_a_failure(self):
        from branding_agents.agents.instagram_draft import run_pack_tool
        import sys

        tmp = Path(tempfile.mkdtemp())
        ok = tmp / "ok.py"
        ok.write_text("import sys,json; print(json.dumps({'verdict':'DA RIVEDERE'})); sys.exit(1)", encoding="utf-8")
        self.assertEqual(run_pack_tool(sys.executable, ok, "x")["verdict"], "DA RIVEDERE")
        bad = tmp / "bad.py"
        bad.write_text("import sys; sys.stderr.write('boom'); sys.exit(2)", encoding="utf-8")
        with self.assertRaises(RuntimeError):
            run_pack_tool(sys.executable, bad, "x")


class TestMemory(Base):
    def test_concurrent_writes_keep_every_entry_and_valid_file(self):
        def work(i):
            for k in range(60):
                self.mem.store_analysis(f"a{i}", "x", {"k": k, "pad": "x" * 300})

        ts = [threading.Thread(target=work, args=(i,)) for i in range(8)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        on_disk = json.loads((self.tmp / "m.json").read_text(encoding="utf-8"))
        self.assertEqual(len(on_disk["entries"]), 480)
        self.assertEqual(self.mem.total_entries, 480)
        self.assertEqual([p.name for p in self.tmp.iterdir()], ["m.json"])  # nessun .tmp rimasto

    def test_failed_save_leaves_previous_file_intact(self):
        self.mem.store_analysis("a", "x", {"v": 1})
        before = (self.tmp / "m.json").read_text(encoding="utf-8")
        from unittest import mock

        with mock.patch("branding_agents.core.util.os.fsync", side_effect=OSError("disco pieno")):
            with self.assertRaises(OSError):
                self.mem.store_analysis("a", "x", {"v": 2})
        self.assertEqual((self.tmp / "m.json").read_text(encoding="utf-8"), before)
        self.assertEqual([p.name for p in self.tmp.iterdir()], ["m.json"])  # il temporaneo è stato rimosso

    def test_corrupt_file_is_quarantined_not_overwritten(self):
        p = self.tmp / "bad.json"
        p.write_text("{ non json", encoding="utf-8")
        m = Memory(p)
        self.assertEqual(m.total_entries, 0)
        m.store_analysis("a", "x", {})
        backups = [f for f in self.tmp.iterdir() if "corrotto" in f.name]
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(encoding="utf-8"), "{ non json")

    def test_legacy_list_format_loads(self):
        p = self.tmp / "old.json"
        p.write_text(json.dumps([{"agent": "x", "action": "a", "data": {}, "recommendations": [], "reflection": "", "timestamp": "2026-01-01T00:00:00"}]), encoding="utf-8")
        self.assertEqual(Memory(p).total_entries, 1)

    def test_cadence_counts_only_successful_runs(self):
        self.assertIsNone(self.mem.days_since("a"))
        self.mem.store_analysis("a", "run", {}, status="error")
        self.assertIsNone(self.mem.days_since("a"))
        old = (datetime.now() - timedelta(days=3)).isoformat(timespec="seconds")
        self.mem.store(MemoryEntry("a", "run", {}, timestamp=old))
        self.assertAlmostEqual(self.mem.days_since("a"), 3, delta=0.01)

    def test_seen_persists(self):
        self.mem.mark_seen("https://ex.com/a")
        self.assertTrue(Memory(self.tmp / "m.json").has_seen("https://ex.com/a"))


class TestPipeline(Base):
    def test_full_pipeline_ok_and_invented_link_dropped(self):
        orch = self.orch(good_script())
        report = orch.run("full")
        by = {r["agent_name"]: r for r in report["results"]}
        self.assertEqual(by["news_scout"]["status"], STATUS_OK)
        self.assertEqual([i["link"] for i in by["news_scout"]["data"]["items"]], ["https://ex.com/a"])
        self.assertTrue(any("scartate" in p for p in by["news_scout"]["problems"]))
        self.assertEqual(by["instagram_draft"]["status"], STATUS_OK)
        self.assertEqual(by["newsletter_draft"]["status"], STATUS_SKIPPED)  # servono 3 notizie in memoria
        self.assertTrue(report["gate"]["approved"])
        self.assertEqual(report["drafts"][0]["status"], "pronta_per_approvazione_umana")
        self.assertTrue(report["human_approval_required"])
        self.assertTrue(self.mem.has_seen("https://ex.com/a"))
        self.assertIn("Report agenti", render_markdown(report))

    def test_llm_garbage_makes_agent_error_not_ok(self):
        script = good_script()
        script["news_scout"] = LLMParseError("rotto")
        report = self.orch(script).run("full")
        by = {r["agent_name"]: r for r in report["results"]}
        self.assertEqual(by["news_scout"]["status"], STATUS_ERROR)
        self.assertEqual(by["instagram_draft"]["status"], STATUS_SKIPPED)  # la sorgente è fallita
        self.assertEqual(report["drafts"], [])
        self.assertFalse(report["gate"]["approved"])
        self.assertEqual(self.mem.days_since("news_scout"), None)  # il fallimento non conta come run riuscita

    def test_missing_required_key_is_error(self):
        script = good_script()
        script["news_scout"] = {"summary": "senza items"}
        by = {r["agent_name"]: r for r in self.orch(script).run("quick")["results"]}
        self.assertEqual(by["news_scout"]["status"], STATUS_ERROR)

    def test_reflection_is_fail_closed(self):
        cases = {
            "llm_down": LLMError("giù"),
            "no_key": {"issues": []},
            "truthy_string": {"approved": "true"},
            "explicit_false": {"approved": False, "issues": ["numero inventato"]},
        }
        for label, review in cases.items():
            self.mem._entries.clear()
            self.mem._seen.clear()  # ogni caso riparte da memoria vuota, anche dopo un fallimento
            with self.subTest(label):
                script = good_script()
                script["reviewer"] = review
                report = self.orch(script).run("full")
                self.assertFalse(report["gate"]["approved"])
                self.assertTrue(report["drafts"])
                self.assertTrue(all(d["status"] != "pronta_per_approvazione_umana" for d in report["drafts"]))

    def test_deterministic_problems_override_llm_approval(self):
        script = good_script()
        script["instagram_draft"]["drafts"][0]["caption"] = "x #a #b #c #d #e #f"
        report = self.orch(script).run("full")
        self.assertTrue(report["gate"]["approved"])  # l'LLM approva...
        self.assertEqual(report["drafts"][0]["status"], "da_rivedere")  # ...ma il codice no

    def test_blocked_indexes_respected(self):
        script = good_script()
        script["reviewer"] = {"approved": True, "issues": [], "blocked_indexes": [0]}
        report = self.orch(script).run("full")
        self.assertEqual(report["drafts"][0]["status"], "bloccata")

    def test_synthesis_failure_falls_back(self):
        script = good_script()
        script["synthesizer"] = LLMError("giù")
        report = self.orch(script).run("full")
        self.assertFalse(report["synthesis_ok"])
        self.assertIn("Bozze:", report["executive_summary"])

    def test_smart_plan_follows_cadence_and_adds_dependencies(self):
        orch = self.orch(good_script())
        self.assertEqual(set(orch.plan("smart")), set(REGISTRY))  # memoria vuota: tutto in scadenza
        now = datetime.now().isoformat(timespec="seconds")
        for n in REGISTRY:
            self.mem.store(MemoryEntry(n, "run", {}, timestamp=now))
        self.assertEqual(orch.plan("smart"), [])  # appena eseguiti
        old = (datetime.now() - timedelta(days=2)).isoformat(timespec="seconds")
        mem2 = Memory(self.tmp / "m2.json")
        mem2.store(MemoryEntry("news_scout", "run", {}, timestamp=now))
        mem2.store(MemoryEntry("newsletter_draft", "run", {}, timestamp=now))
        mem2.store(MemoryEntry("instagram_draft", "run", {}, timestamp=old))
        plan = Orchestrator(FakeLLM(good_script()), mem2, self.cfg).plan("smart")
        self.assertEqual(plan, ["news_scout", "instagram_draft"])  # la dipendenza viene aggiunta e precede

    def test_nothing_to_do_makes_no_llm_calls(self):
        now = datetime.now().isoformat(timespec="seconds")
        for n in REGISTRY:
            self.mem.store(MemoryEntry(n, "run", {}, timestamp=now))
        orch = self.orch(good_script())
        report = orch.run("smart")
        self.assertTrue(report["nothing_to_do"])
        self.assertEqual(orch.llm.calls, [])

    def test_no_feeds_configured_is_skipped_not_error(self):
        self.cfg.feeds = []
        by = {r["agent_name"]: r for r in self.orch(good_script()).run("quick")["results"]}
        self.assertEqual(by["news_scout"]["status"], STATUS_SKIPPED)

    def test_all_feeds_failing_is_error(self):
        def boom(url, timeout=15):
            raise OSError("irraggiungibile")

        NewsScout.fetcher = staticmethod(boom)
        by = {r["agent_name"]: r for r in self.orch(good_script()).run("quick")["results"]}
        self.assertEqual(by["news_scout"]["status"], STATUS_ERROR)

    def test_newsletter_uses_memory_of_last_week(self):
        for i, link in enumerate(["https://ex.com/a", "https://ex.com/b", "https://ex.com/c"]):
            self.mem.store_analysis("news_scout", "run", {"items": [{"title": f"t{i}", "link": link}]})
        script = good_script()
        script["newsletter_draft"]["drafts"][0]["links"] = ["https://ex.com/a", "https://ex.com/ZZZ"]
        report = self.orch(script).run("full")
        nl = [d for d in report["drafts"] if d["platform"] == "newsletter"][0]
        self.assertTrue(any("link non presenti" in p for p in nl["problems"]))
        self.assertEqual(nl["status"], "da_rivedere")


if __name__ == "__main__":
    unittest.main()
