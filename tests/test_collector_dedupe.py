import ast
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
COLLECTOR = ROOT / "collector" / "collector.py"


def load_dedupe_channels():
    tree = ast.parse(COLLECTOR.read_text(encoding="utf-8"))
    function = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "dedupe_channels"
    )
    namespace = {}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(COLLECTOR), "exec"), namespace)
    return namespace["dedupe_channels"]


class CollectorDedupeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dedupe = staticmethod(load_dedupe_channels())

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

    def test_ignores_rows_without_identity(self):
        rows = [{"id": 1, "telegram_id": "", "username": ""}, {"id": 2}]
        self.assertEqual(self.dedupe(rows), [])


if __name__ == "__main__":
    unittest.main()
