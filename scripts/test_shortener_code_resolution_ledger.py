import json
import tempfile
import unittest
from pathlib import Path

import shortener_code_resolution_ledger as ledger


class ShortenerCodeResolutionLedgerTests(unittest.TestCase):
    def test_extract_code_from_nested_reader_url(self):
        self.assertEqual(ledger.extract_code("https://r.jina.ai/https://da.gd/Ab_9-x"), "da.gd/Ab_9-x")

    def test_build_classifies_recovered_stub_and_missing(self):
        payload = {
            "hosts": {"reader": {"rows": [
                {"class": "shortener-code", "url": "https://r.jina.ai/https://da.gd/a", "ts": "1", "status": "200"},
                {"class": "shortener-code", "url": "https://r.jina.ai/https://tinyurl.com/b", "ts": "2", "status": "200"},
                {"class": "shortener-code", "url": "https://r.jina.ai/https://is.gd/c", "ts": "3", "status": "451"},
            ]}},
            "shortener_code_resolution": {"resolved": {
                "da.gd/a": "target recovered",
                "tinyurl.com/b": "preview/deprecated stub, target not recoverable",
                "da.gd/hop": "intermediate target",
                "da.gd/fbKv": "discovered hop",
            }},
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.json"
            path.write_text(json.dumps(payload))
            report = ledger.build(path)
        self.assertEqual(report["scope"]["unique_source_codes"], 4)
        self.assertEqual(report["counts"]["archived-target-recovered"], 2)
        self.assertEqual(report["counts"]["archived-stub-target-unrecoverable"], 1)
        self.assertEqual(report["counts"]["no-archive-capture-found-in-2026-09-08-pass"], 1)
        self.assertEqual(report["scope"]["discovered_intermediate_hops"], 1)

    def test_committed_ledger_disposes_every_code(self):
        report = json.loads(ledger.OUTPUT.read_text())
        self.assertEqual(len(report["codes"]), report["scope"]["unique_source_codes"])
        self.assertEqual(sum(report["counts"].values()), report["scope"]["unique_source_codes"])
        self.assertTrue(all(row["disposition"] for row in report["codes"]))


if __name__ == "__main__":
    unittest.main()
