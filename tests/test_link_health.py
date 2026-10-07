import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKER = (ROOT / "worker" / "src" / "index.js").read_text(encoding="utf-8")
COLLECTOR = (ROOT / "collector" / "collector.py").read_text(encoding="utf-8")
SCHEMA = (ROOT / "worker" / "schema.sql").read_text(encoding="utf-8")
WORKFLOW = (ROOT / ".github" / "workflows" / "collector.yml").read_text(encoding="utf-8")


class LinkHealthStaticTests(unittest.TestCase):
    def test_schema_has_deduplicated_health_records(self):
        self.assertIn("CREATE TABLE IF NOT EXISTS link_health", SCHEMA)
        self.assertIn("url TEXT PRIMARY KEY", SCHEMA)
        self.assertIn("checked_at TEXT", SCHEMA)
        self.assertIn("idx_link_health_check", SCHEMA)

    def test_worker_extracts_and_records_urls_during_ingest(self):
        self.assertIn("const HTTP_URL_RE=", WORKER)
        self.assertIn("async function recordMessageLinks", WORKER)
        self.assertIn("await recordMessageLinks(env,normalized)", WORKER)
        self.assertIn("ON CONFLICT(url) DO UPDATE", WORKER)

    def test_collector_health_routes_are_collector_protected(self):
        self.assertIn("/api/collector/link-health", WORKER)
        self.assertIn("async function collectorLinkCandidates", WORKER)
        self.assertIn("async function recordLinkHealth", WORKER)
        self.assertGreaterEqual(WORKER.count("if(!requireCollector(req,env))return json({error:\"unauthorized\"},401);"), 2)

    def test_collector_checks_only_safe_external_http_urls(self):
        self.assertIn("def safe_external_url", COLLECTOR)
        self.assertIn("ip.is_private", COLLECTOR)
        self.assertIn("ip.is_loopback", COLLECTOR)
        self.assertIn("follow_redirects=False", COLLECTOR)
        self.assertIn("Range", COLLECTOR)
        self.assertIn("t.me/", COLLECTOR)

    def test_workflow_runs_bounded_health_checks(self):
        for key in (
            "LINK_CHECK_ENABLED: 1",
            "LINK_CHECK_LIMIT: 80",
            "LINK_CHECK_MAX_AGE_HOURS: 24",
            "LINK_CHECK_TIMEOUT_SECONDS: 12",
            "LINK_CHECK_CONCURRENCY: 8",
        ):
            self.assertIn(key, WORKFLOW)
        self.assertIn("Link health:", WORKFLOW)


if __name__ == "__main__":
    unittest.main()
