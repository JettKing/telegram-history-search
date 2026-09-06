import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKER = (ROOT / "worker" / "src" / "index.js").read_text(encoding="utf-8")
SCHEMA = (ROOT / "worker" / "schema.sql").read_text(encoding="utf-8")


class WorkerDeduplicationStaticTests(unittest.TestCase):
    def test_existing_channel_lookup_prefers_telegram_id(self):
        self.assertIn("async function findExistingChannel", WORKER)
        self.assertIn("WHERE telegram_id=? LIMIT 1", WORKER)
        self.assertIn("WHERE username=? LIMIT 1", WORKER)

    def test_add_paths_share_existing_channel_lookup(self):
        self.assertGreaterEqual(WORKER.count("const existing=await findExistingChannel(env,identity)"), 2)

    def test_ingest_normalizes_channel_id_before_message_write(self):
        self.assertIn("const normalized=messages.slice(0,500).map", WORKER)
        self.assertIn("channel_id:cid", WORKER)
        self.assertIn("ON CONFLICT(channel_id,message_id)DO UPDATE", WORKER)

    def test_channel_lists_dedupe_without_deleting_rows(self):
        start = WORKER.index("async function channels(")
        end = WORKER.index("async function saveSession", start)
        channel_list_code = WORKER[start:end]
        self.assertIn("const chosen=new Map()", channel_list_code)
        self.assertIn("return [...chosen.values()]", channel_list_code)
        self.assertNotIn("DELETE FROM channels", channel_list_code)
        self.assertNotIn("DELETE FROM messages", channel_list_code)

    def test_existing_public_api_routes_remain_present(self):
        for route in (
            'u.pathname==="/api/search"',
            'u.pathname==="/api/latest"',
            'u.pathname==="/api/channels"',
            'u.pathname==="/api/ingest"',
            'u.pathname==="/api/collector/channels"',
        ):
            self.assertIn(route, WORKER)

    def test_database_message_uniqueness_is_preserved(self):
        self.assertIn("UNIQUE(channel_id, message_id)", SCHEMA)


if __name__ == "__main__":
    unittest.main()
