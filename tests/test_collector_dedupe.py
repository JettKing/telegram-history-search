import ast
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
COLLECTOR = ROOT / "collector" / "collector.py"


def load_dedupe_channels():
    tree = ast.parse(COLLECTOR.read_text(encoding="utf-8"))
    functions = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name in {"normalize_username", "dedupe_channels"}
    ]
    namespace = {}
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(COLLECTOR), "exec"), namespace)
    return namespace["dedupe_channels"]


def load_normalize_search_text():
    tree = ast.parse(COLLECTOR.read_text(encoding="utf-8"))
    functions = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "normalize_search_text"
    ]
    namespace = {}
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(COLLECTOR), "exec"), namespace)
    return namespace["normalize_search_text"]


class CollectorDedupeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dedupe = staticmethod(load_dedupe_channels())
        cls.normalize_search_text = staticmethod(load_normalize_search_text())

    def test_prefers_record_with_existing_messages(self):
        rows = [
            {"id": 31, "telegram_id": "xph_fx", "username": "xph_fx", "message_count": 0, "last_message_id": 0},
            {"id": 32, "telegram_id": "1895388077", "username": "xph_fx", "message_count": 3293, "last_message_id": 4853},
        ]
        result = self.dedupe(rows)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["telegram_id"], "1895388077")

    def test_prefers_latest_message_id_when_counts_match(self):
        rows = [
            {"id": 1, "telegram_id": "100", "username": "demo", "message_count": 10, "last_message_id": 12},
            {"id": 2, "telegram_id": "200", "username": "demo", "message_count": 10, "last_message_id": 13},
        ]
        result = self.dedupe(rows)
        self.assertEqual(result[0]["telegram_id"], "200")

    def test_accepts_at_prefix_and_keeps_distinct_channels(self):
        rows = [
            {"id": 1, "telegram_id": "100", "username": "@demo", "message_count": 1},
            {"id": 2, "telegram_id": "200", "username": "other", "message_count": 2},
        ]
        result = self.dedupe(rows)
        self.assertEqual({row["telegram_id"] for row in result}, {"100", "200"})

    def test_merges_usernames_that_only_differ_by_case_or_at_prefix(self):
        rows = [
            {"id": 1, "telegram_id": "100", "username": "@DemoChannel", "message_count": 1},
            {"id": 2, "telegram_id": "200", "username": "demochannel", "message_count": 2},
        ]
        result = self.dedupe(rows)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["telegram_id"], "200")

    def test_ignores_rows_without_identity(self):
        rows = [{"id": 1, "telegram_id": "", "username": ""}, {"id": 2}]
        self.assertEqual(self.dedupe(rows), [])

    def test_normalizes_search_text_case_and_whitespace(self):
        self.assertEqual(self.normalize_search_text("  Hello   WORLD  "), "hello world")
        self.assertEqual(self.normalize_search_text("ÄÖÜ"), "äöü")

    def test_collector_contains_resume_and_flood_wait_controls(self):
        source = COLLECTOR.read_text(encoding="utf-8")
        self.assertIn("FloodWaitError", source)
        self.assertIn("MAX_FLOOD_WAIT_SECONDS", source)
        self.assertIn('channel["last_message_id"]', source)


if __name__ == "__main__":
    unittest.main()
