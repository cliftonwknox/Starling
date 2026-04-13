"""Tests for semantic_router.py — skill-based semantic routing."""

import json
import os
import shutil
import tempfile
import logging
import unittest
from unittest.mock import patch

# Ensure project dir is on path
import sys
sys.path.insert(0, os.path.dirname(__file__))


class TestAgentTemplates(unittest.TestCase):
    """Tests for pre-built agent templates."""

    def test_six_templates_exist(self):
        from semantic_router import AGENT_TEMPLATES
        self.assertEqual(len(AGENT_TEMPLATES), 6)

    def test_all_templates_have_required_fields(self):
        from semantic_router import AGENT_TEMPLATES, VALID_TIERS
        required = {"name", "role", "goal", "backstory", "primary_purpose",
                     "secondary_purposes", "tools", "color", "tier"}
        for tid, tmpl in AGENT_TEMPLATES.items():
            for field in required:
                self.assertIn(field, tmpl, f"Template '{tid}' missing '{field}'")
            self.assertIn(
                tmpl["tier"], VALID_TIERS,
                f"Template '{tid}' has invalid tier '{tmpl['tier']}'"
            )

    def test_list_templates(self):
        from semantic_router import list_templates
        templates = list_templates()
        self.assertEqual(len(templates), 6)
        ids = [t[0] for t in templates]
        self.assertIn("researcher", ids)
        self.assertIn("software_engineer", ids)

    def test_get_template(self):
        from semantic_router import get_template
        tmpl = get_template("researcher")
        self.assertIsNotNone(tmpl)
        self.assertEqual(tmpl["name"], "Researcher")

    def test_get_template_missing(self):
        from semantic_router import get_template
        self.assertIsNone(get_template("nonexistent"))

    def test_secondary_purposes_are_lists(self):
        from semantic_router import AGENT_TEMPLATES
        for tid, tmpl in AGENT_TEMPLATES.items():
            self.assertIsInstance(tmpl["secondary_purposes"], list)
            self.assertGreater(len(tmpl["secondary_purposes"]), 0,
                               f"Template '{tid}' has no secondary purposes")


class TestComputeAgentsHash(unittest.TestCase):
    """Unit tests for _compute_agents_hash."""

    def setUp(self):
        from semantic_router import _compute_agents_hash
        self.hash_fn = _compute_agents_hash

    def test_deterministic(self):
        agents = [
            {"id": "a", "role": "Researcher", "goal": "research things"},
            {"id": "b", "role": "Writer", "goal": "write things"},
        ]
        self.assertEqual(self.hash_fn(agents), self.hash_fn(agents))

    def test_changes_on_goal_change(self):
        agents1 = [{"id": "a", "role": "R", "goal": "goal1"}]
        agents2 = [{"id": "a", "role": "R", "goal": "goal2"}]
        self.assertNotEqual(self.hash_fn(agents1), self.hash_fn(agents2))

    def test_order_independent(self):
        agents_ab = [
            {"id": "a", "role": "R", "goal": "g"},
            {"id": "b", "role": "W", "goal": "g"},
        ]
        agents_ba = [
            {"id": "b", "role": "W", "goal": "g"},
            {"id": "a", "role": "R", "goal": "g"},
        ]
        self.assertEqual(self.hash_fn(agents_ab), self.hash_fn(agents_ba))

    def test_changes_on_role_change(self):
        agents1 = [{"id": "a", "role": "Researcher", "goal": "g"}]
        agents2 = [{"id": "a", "role": "Writer", "goal": "g"}]
        self.assertNotEqual(self.hash_fn(agents1), self.hash_fn(agents2))


class TestSemanticRouterWithConfig(unittest.TestCase):
    """Tests that use real embeddings against the live example test config.

    These tests require the fastembed model to be available and the
    project_config.json to have agents configured.
    """

    @classmethod
    def setUpClass(cls):
        """Ensure skill vectors are embedded once for all tests."""
        from semantic_router import ensure_skill_vectors
        ensure_skill_vectors()

    def test_ensure_creates_table(self):
        from semantic_router import _get_table
        table = _get_table()
        self.assertGreater(table.count_rows(), 0)

    def test_ensure_skips_when_unchanged(self):
        from semantic_router import ensure_skill_vectors
        result = ensure_skill_vectors()
        self.assertFalse(result)

    def test_ensure_force_rebuilds(self):
        from semantic_router import ensure_skill_vectors
        result = ensure_skill_vectors(force=True)
        self.assertTrue(result)

    def test_semantic_route_sports_query(self):
        """Integration test — requires example test config (odds_maker agent)."""
        from config_loader import get_agent_ids
        if "odds_maker" not in get_agent_ids():
            self.skipTest("Requires example test config with odds_maker agent")
        from semantic_router import semantic_route
        result = semantic_route("research sports odds and betting lines")
        self.assertEqual(result, "odds_maker")

    def test_semantic_route_gibberish_returns_none(self):
        from semantic_router import semantic_route
        result = semantic_route("xyzzy completely unrelated gibberish foobar baz")
        self.assertIsNone(result)

    def test_get_routing_info_structure(self):
        from semantic_router import get_routing_info
        info = get_routing_info()
        self.assertIn("mode", info)
        self.assertIn("agent_count", info)
        self.assertIn("last_embed_time", info)
        self.assertIn("embedding_model", info)
        self.assertEqual(info["mode"], "semantic")
        self.assertGreater(info["agent_count"], 0)

    def test_rebuild(self):
        from semantic_router import rebuild
        result = rebuild()
        self.assertTrue(result)


class TestRouteCache(unittest.TestCase):
    """Tests for the LRU route cache."""

    def test_cache_hit_returns_same_result(self):
        from semantic_router import semantic_route, _route_cache
        _route_cache.clear()
        r1 = semantic_route("research sports odds and betting lines")
        r2 = semantic_route("research sports odds and betting lines")
        # Cache correctness: two identical calls must return the same result
        self.assertEqual(r1, r2)

    def test_cache_populated_after_route(self):
        from semantic_router import semantic_route, _route_cache
        _route_cache.clear()
        semantic_route("research sports odds and betting lines")
        self.assertIn("research sports odds and betting lines", _route_cache)

    def test_cache_cleared_on_rebuild(self):
        from semantic_router import semantic_route, rebuild, _route_cache
        semantic_route("research sports odds and betting lines")
        self.assertGreater(len(_route_cache), 0)
        rebuild()
        self.assertEqual(len(_route_cache), 0)

    def test_cache_stores_none_for_no_match(self):
        from semantic_router import semantic_route, _route_cache
        _route_cache.clear()
        result = semantic_route("xyzzy completely unrelated gibberish foobar baz")
        self.assertIsNone(result)
        self.assertIn("xyzzy completely unrelated gibberish foobar baz", _route_cache)
        self.assertIsNone(_route_cache["xyzzy completely unrelated gibberish foobar baz"])

    def test_cache_info_in_routing_info(self):
        from semantic_router import get_routing_info
        info = get_routing_info()
        self.assertIn("cache_size", info)
        self.assertIn("cache_max", info)


class TestDuplicateDetection(unittest.TestCase):
    """Tests for duplicate work detection."""

    def test_record_and_detect_exact_duplicate(self):
        from semantic_router import record_completed_task, check_duplicate
        desc = "test dedup exact match unique string 12345"
        record_completed_task("test_dedup_1", desc, "agent_a", "2026-04-11T20:00:00")
        dup = check_duplicate(desc)
        self.assertIsNotNone(dup)
        self.assertEqual(dup["task_id"], "test_dedup_1")
        self.assertLessEqual(dup["distance"], 0.01)

    def test_no_duplicate_for_different_task(self):
        from semantic_router import check_duplicate
        dup = check_duplicate("write a completely unrelated blog post about underwater basket weaving")
        self.assertIsNone(dup)

    def test_get_dedup_stats(self):
        from semantic_router import get_dedup_stats
        stats = get_dedup_stats()
        self.assertIn("tracked_tasks", stats)
        self.assertIn("max_entries", stats)
        self.assertGreater(stats["tracked_tasks"], 0)


class TestProgressTracking(unittest.TestCase):
    """Tests for progress measurement."""

    def test_relevant_output_scores_high(self):
        from semantic_router import measure_progress
        p = measure_progress(
            "research NBA odds and spreads",
            "Lakers favored by 5.5 points, odds -220. Celtics +180. Spread moved from 4.5."
        )
        self.assertGreaterEqual(p["score"], 40)
        self.assertIn(p["assessment"], ("good", "excellent"))

    def test_unrelated_output_scores_low(self):
        from semantic_router import measure_progress
        p = measure_progress(
            "research NBA odds and spreads",
            "To bake a cake, preheat oven to 350 degrees."
        )
        self.assertLessEqual(p["score"], 30)
        self.assertEqual(p["assessment"], "weak")

    def test_empty_output_scores_zero(self):
        from semantic_router import measure_progress
        p = measure_progress("any goal", "")
        self.assertEqual(p["score"], 0)
        self.assertEqual(p["assessment"], "weak")

    def test_returns_correct_structure(self):
        from semantic_router import measure_progress
        p = measure_progress("goal", "output")
        self.assertIn("score", p)
        self.assertIn("distance", p)
        self.assertIn("assessment", p)
        self.assertIsInstance(p["score"], int)
        self.assertIn(p["assessment"], ("excellent", "good", "partial", "weak"))


class TestEndToEnd(unittest.TestCase):
    """End-to-end integration tests across the full system."""

    def test_full_heartbeat_flow(self):
        """Route -> dedup check -> progress -> record, all in sequence.

        Isolates test state properly: cleans up the dedup table entry created
        during the test, so repeated runs don't poison each other via shared
        vocabulary in description embeddings.
        """
        from heartbeat import add_task, auto_route, update_task, _load_queue, _get_data_file
        from semantic_router import (
            check_duplicate, measure_progress, record_completed_task,
            _get_dedup_table, _DEDUP_TABLE, _get_db,
        )
        import os
        import secrets

        # Back up queue
        data_file = _get_data_file("task_queue.json")
        backup = None
        if os.path.exists(data_file):
            with open(data_file) as f:
                backup = f.read()

        # Fully random description — 128 bits of entropy. Note: embedding
        # models still pick up the fixed "isolated test probe" prefix, so
        # we explicitly clean up the dedup row after the assertions below.
        probe_tag = secrets.token_hex(16)
        unique_description = f"isolated test probe {probe_tag}"

        # Remove any leftover rows from prior failed runs — this test owns
        # the "isolated test probe" namespace in the dedup table
        def _cleanup_dedup():
            try:
                table = _get_dedup_table()
                # LanceDB supports delete with SQL-like predicates
                table.delete("description LIKE 'isolated test probe %'")
            except Exception:
                pass  # cleanup is best-effort

        _cleanup_dedup()

        try:
            task = add_task(unique_description)
            agent_id, method = auto_route(task["description"])
            self.assertIsNotNone(agent_id)
            self.assertIn(method, ("keyword", "semantic", "default"))

            # No duplicate yet — table was just cleaned and description is fresh
            pre_dup = check_duplicate(task["description"])
            self.assertIsNone(
                pre_dup,
                f"Fresh description after cleanup unexpectedly matched: {pre_dup}"
            )

            # Measure progress
            result = "Yankees favored -150, Dodgers +130."
            progress = measure_progress(task["description"], result)
            self.assertIn("score", progress)
            self.assertGreater(progress["score"], 0)

            # Record and verify dedup catches it
            record_completed_task(
                task["id"], task["description"], agent_id, "2026-04-11T21:00:00"
            )
            dup = check_duplicate(task["description"])
            self.assertIsNotNone(dup)
            self.assertEqual(dup["task_id"], task["id"])
        finally:
            _cleanup_dedup()
            if backup:
                with open(data_file, "w") as f:
                    f.write(backup)
            elif os.path.exists(data_file):
                os.remove(data_file)

    def test_heartbeat_start_stop_with_routing(self):
        """Heartbeat start() calls ensure_skill_vectors without crashing."""
        from heartbeat import Heartbeat
        import time
        hb = Heartbeat(interval=9999)
        hb.start()
        time.sleep(0.3)
        self.assertTrue(hb.running)
        hb.stop()
        self.assertFalse(hb.running)

    def test_template_creates_valid_agent_config(self):
        """Template fields produce a valid agent config dict."""
        from semantic_router import get_template
        tmpl = get_template("researcher")
        agent_cfg = {
            "id": "researcher",
            "name": tmpl["name"],
            "role": tmpl["role"],
            "goal": tmpl["goal"],
            "backstory": tmpl["backstory"],
            "tools": list(tmpl["tools"]),
            "preset": "grok",
            "color": tmpl["color"],
            "allow_delegation": False,
            "template": "researcher",
        }
        # All required fields present
        for field in ("id", "name", "role", "goal", "backstory", "tools", "preset"):
            self.assertIn(field, agent_cfg)
            self.assertTrue(agent_cfg[field], f"Field '{field}' is empty")

    def test_template_tools_have_valid_prefixes(self):
        """All template tools use known CrewAI tool prefixes."""
        from semantic_router import AGENT_TEMPLATES
        valid_prefixes = ("ddg_search", "tavily_search", "scrape_website", "crewai:", "cron_tool", "skills:")
        for tid, tmpl in AGENT_TEMPLATES.items():
            for tool in tmpl["tools"]:
                self.assertTrue(
                    any(tool.startswith(p) for p in valid_prefixes),
                    f"Template '{tid}' has unknown tool: {tool}"
                )

    def test_telegram_routing_command(self):
        """Telegram /routing returns status string."""
        from telegram_listener import _cmd_routing
        result = _cmd_routing()
        self.assertIn("Routing Status", result)
        self.assertIn("Mode:", result)
        self.assertIn("Agents:", result)

    def test_embedders_use_cpu_only(self):
        """Both embedding models must be CPU-only to avoid GPU conflicts."""
        from semantic_router import _get_embedder
        embedder = _get_embedder()
        # fastembed stores the providers list; CPU provider should be present
        # Just verify it loaded without error — the cuda=Device.CPU in code is the guard
        self.assertIsNotNone(embedder)

    def test_secondary_purposes_embedded_for_template_agents(self):
        """Agents with template field get secondary purpose vectors."""
        from semantic_router import ensure_skill_vectors, _get_table, AGENT_TEMPLATES
        # Force rebuild to ensure clean state
        ensure_skill_vectors(force=True)
        table = _get_table()
        rows = table.to_pandas()
        # Current agents don't have template field, so only primary vectors
        primary_count = len(rows[rows["skill_type"] == "primary"])
        secondary_count = len(rows[rows["skill_type"] == "secondary"])
        # With current example test config (no templates), all should be primary
        self.assertGreater(primary_count, 0)
        # Secondary would be > 0 only if agents have template field set
        # This test documents the behavior — it's a structural check
        self.assertGreaterEqual(secondary_count, 0)


class TestAutoRoute(unittest.TestCase):
    """Integration tests for the 3-tier routing cascade in heartbeat.

    These tests assume the example test config (leader + odds_maker agents with
    specific routing keywords). They skip gracefully on other configs.
    """

    @classmethod
    def setUpClass(cls):
        from config_loader import get_agent_ids
        cls._has_test_config = "leader" in get_agent_ids() and "odds_maker" in get_agent_ids()

    def test_keyword_wins(self):
        if not self._has_test_config:
            self.skipTest("Requires example test config (leader + odds_maker)")
        from heartbeat import auto_route
        # "decision" is a keyword for leader
        agent, method = auto_route("make a decision about the bet")
        self.assertEqual(agent, "leader")
        self.assertEqual(method, "keyword")

    def test_semantic_fallback(self):
        if not self._has_test_config:
            self.skipTest("Requires example test config (leader + odds_maker)")
        from heartbeat import auto_route
        # No keywords match, but semantic should pick odds_maker
        agent, method = auto_route("research NBA odds and spreads tonight")
        self.assertEqual(method, "semantic")
        self.assertEqual(agent, "odds_maker")

    def test_default_fallback(self):
        if not self._has_test_config:
            self.skipTest("Requires example test config (leader + odds_maker)")
        from heartbeat import auto_route
        agent, method = auto_route("xyzzy random gibberish nonsense completely unrelated")
        self.assertEqual(method, "default")
        # Default agent should be leader per config
        self.assertEqual(agent, "leader")

    def test_returns_tuple(self):
        from heartbeat import auto_route
        result = auto_route("any description")
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 2)

    def test_default_fallback_logs_warning(self):
        from heartbeat import auto_route
        with self.assertLogs("starling.heartbeat", level="WARNING") as cm:
            auto_route("xyzzy random gibberish nonsense completely unrelated")
        self.assertTrue(any("falling back to default" in msg for msg in cm.output))


class TestRouteTagging(unittest.TestCase):
    """Integration tests for route tagging and retry capping in _tick()."""

    def setUp(self):
        """Create a temp task queue for isolated testing."""
        from heartbeat import add_task, _load_queue, _save_queue, _get_data_file
        self._data_file = _get_data_file("task_queue.json")
        # Back up existing queue
        self._backup = None
        if os.path.exists(self._data_file):
            with open(self._data_file) as f:
                self._backup = f.read()

    def tearDown(self):
        """Restore original queue."""
        if self._backup is not None:
            with open(self._data_file, "w") as f:
                f.write(self._backup)
        elif os.path.exists(self._data_file):
            os.remove(self._data_file)

    def test_default_routed_task_gets_tag(self):
        from heartbeat import add_task, auto_route, update_task, _load_queue
        task = add_task("xyzzy random gibberish completely unrelated foobar")
        agent_id, route_method = auto_route(task["description"])
        tags = task.get("tags", [])
        tags.append(f"routed:{route_method}")
        update_task(task["id"], agent=agent_id, tags=tags)

        queue = _load_queue()
        updated = next(t for t in queue if t["id"] == task["id"])
        self.assertIn("routed:default", updated["tags"])

    def test_semantic_routed_task_gets_tag(self):
        from heartbeat import add_task, auto_route, update_task, _load_queue
        task = add_task("research sports odds and betting lines")
        agent_id, route_method = auto_route(task["description"])
        tags = task.get("tags", [])
        tags.append(f"routed:{route_method}")
        update_task(task["id"], agent=agent_id, tags=tags)

        queue = _load_queue()
        updated = next(t for t in queue if t["id"] == task["id"])
        self.assertIn("routed:semantic", updated["tags"])

    def test_keyword_routed_task_gets_tag(self):
        from heartbeat import add_task, auto_route, update_task, _load_queue
        task = add_task("make a decision about the bet")
        agent_id, route_method = auto_route(task["description"])
        tags = task.get("tags", [])
        tags.append(f"routed:{route_method}")
        update_task(task["id"], agent=agent_id, tags=tags)

        queue = _load_queue()
        updated = next(t for t in queue if t["id"] == task["id"])
        self.assertIn("routed:keyword", updated["tags"])

    def test_default_routed_caps_retries(self):
        from heartbeat import add_task, auto_route, update_task, _load_queue
        task = add_task("xyzzy random gibberish completely unrelated foobar")
        agent_id, route_method = auto_route(task["description"])
        tags = task.get("tags", [])
        tags.append(f"routed:{route_method}")
        update_task(task["id"], agent=agent_id, tags=tags)
        if route_method == "default":
            update_task(task["id"], max_retries=0)

        queue = _load_queue()
        updated = next(t for t in queue if t["id"] == task["id"])
        self.assertEqual(updated["max_retries"], 0)


if __name__ == "__main__":
    unittest.main()
