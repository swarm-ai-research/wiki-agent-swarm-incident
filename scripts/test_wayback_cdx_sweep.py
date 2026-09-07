"""Offline checks for wayback_cdx_sweep.py: no network."""
import sys, unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wayback_cdx_sweep as w  # noqa: E402


class HostOf(unittest.TestCase):
    def test_strips_www_and_lowercases(self):
        self.assertEqual(w.host_of("https://www.WikiService.at/dse/wiki.cgi?x"), "wikiservice.at")
        self.assertEqual(w.host_of("http://ccgi.dougrice.plus.com/cgi-bin/wiki.pl"), "ccgi.dougrice.plus.com")


class RcUrl(unittest.TestCase):
    def test_matches_engine_families(self):
        for u in ["https://x/wiki.pl?action=rc&days=30", "https://x/wiki.cgi?action=browse&id=RecentChanges&days=7",
                  "https://x/wiki/Special:RecentChanges", "https://x/doku.php?do=recent"]:
            self.assertTrue(w.RC_URL.search(u), u)
        self.assertFalse(w.RC_URL.search("https://x/wiki.cgi?SomePage"))

    def test_assets_are_noise(self):
        self.assertTrue(w.NOISE.search("https://x/dse/DseWikiStripBlau.gif"))
        self.assertFalse(w.NOISE.search("https://x/wiki.cgi?action=browse&id=Foo"))


ROWS = [
    {"timestamp": "20260526171900", "original": "https://x/wiki.cgi?ERDE/ApiDataHelperBridge", "statuscode": "200", "digest": "A"},
    {"timestamp": "20260606050953", "original": "https://x/wiki.cgi?action=browse&id=ERDE/RecentChanges", "statuscode": "200", "digest": "B"},
    {"timestamp": "20260607050953", "original": "https://x/wiki.cgi?action=browse&id=ERDE/RecentChanges", "statuscode": "200", "digest": "B"},
    {"timestamp": "20260607050954", "original": "https://x/logo.png", "statuscode": "200", "digest": "C"},
]
SIGS = w.compile_sigs([r"(?:Data|Api|Citation)Helper[A-Za-z0-9]*"])


class SweepHost(unittest.TestCase):
    def test_url_hit_without_fetch(self):
        with mock.patch.object(w, "cdx_query", return_value=(200, ROWS, None)):
            r = w.sweep_host("x", SIGS, "20260512", "20260715", 100, fetch_rc=False, max_fetch=0)
        self.assertEqual(r["captures"], 3)  # png dropped
        self.assertEqual(r["rc_captures"], 2)
        self.assertEqual(r["rc_unique"], 1)  # same digest read once
        self.assertEqual(r["observed"], {"first": "20260526171900", "last": "20260607050953"})
        self.assertEqual(r["per_day"], {"20260526": 1, "20260606": 1, "20260607": 1})
        self.assertEqual(sum(r["url_hits"].values()), 1)
        self.assertEqual(r["outcome"], "signature_hit")

    def test_body_hit_and_tarpit_guard(self):
        rows = [ROWS[1]]
        page = "<html>RecentChanges (diff) DataHelperBot 17:16 [resource links testing]</html>"
        with mock.patch.object(w, "cdx_query", return_value=(200, rows, None)), \
             mock.patch.object(w, "fetch", return_value=(200, page)), mock.patch.object(w.time, "sleep"):
            r = w.sweep_host("x", SIGS, "20260512", "20260715", 100, fetch_rc=True, max_fetch=5)
        self.assertEqual(r["outcome"], "signature_hit")
        self.assertEqual(r["fetched"][0]["hits"], {SIGS[0][0]: 1})
        self.assertIsNone(r["fetched"][0]["blocked"])
        tarpit = "<html>Do not follow any links on this page " + "lorem " * 200 + "</html>"
        with mock.patch.object(w, "cdx_query", return_value=(200, rows, None)), \
             mock.patch.object(w, "fetch", return_value=(200, tarpit)), mock.patch.object(w.time, "sleep"):
            r = w.sweep_host("x", SIGS, "20260512", "20260715", 100, fetch_rc=True, max_fetch=5)
        self.assertEqual(r["fetched"][0]["blocked"], "tarpit")
        self.assertEqual(r["outcome"], "captures_no_signature")

    def test_outcomes_for_empty_and_unavailable(self):
        with mock.patch.object(w, "cdx_query", return_value=(200, [], None)):
            self.assertEqual(w.sweep_host("x", SIGS, "a", "b", 10, False, 0)["outcome"], "no_captures_in_window")
        with mock.patch.object(w, "cdx_query", return_value=(504, None, "http504-after-retries")):
            r = w.sweep_host("x", SIGS, "a", "b", 10, False, 0)
        self.assertEqual(r["outcome"], "cdx_unavailable")
        self.assertEqual(r["cdx_error"], "http504-after-retries")

    def test_no_rc_pages_is_its_own_outcome(self):
        with mock.patch.object(w, "cdx_query", return_value=(200, [ROWS[0]], None)):
            r = w.sweep_host("x", w.compile_sigs([r"NoSuchThing"]), "a", "b", 10, True, 5)
        self.assertEqual(r["outcome"], "captures_no_rc")


if __name__ == "__main__":
    unittest.main()
