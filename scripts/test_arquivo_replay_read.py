import unittest

import arquivo_replay_read as replay


def row(ts, digest, url="https://example.org/a", status="200", mime="text/plain"):
    return {"timestamp": ts, "digest": digest, "url": url, "status": status, "mime": mime}


class ReplayReadTests(unittest.TestCase):
    def test_replay_url_is_id_replay_even_for_save_now_targets(self):
        url = "https://api.codetabs.com/v1/proxy?quest=https://arquivo.pt/save/now/20260521181216mp_/x"
        self.assertEqual(replay.replay_url(row("20260521183632", "D", url)),
                         "https://arquivo.pt/wayback/20260521183632id_/" + url)

    def test_one_read_per_digest_takes_earliest(self):
        rows = [row("20260526075123", "A"), row("20260526073119", "A"), row("20260526073122", "B")]
        chosen = replay.one_per_digest(rows)
        self.assertEqual([(r["timestamp"], r["digest"]) for r in chosen],
                         [("20260526073119", "A"), ("20260526073122", "B")])

    def test_describe_checks_digest_and_keeps_redirect_unfollowed(self):
        body = b"Year,Value\n2005,1681\n"
        record = replay.describe(row("1", replay.warc_digest(body)), 200,
                                 {"Content-Type": "text/plain"}, body)
        self.assertTrue(record["digest_match"])
        self.assertEqual(record["body_text"], body.decode())
        moved = replay.describe(row("2", "X", status="301", mime="text/html"), 307,
                                {"Location": "https://arquivo.pt/wayback/3id_/y?key=secret"}, b"")
        self.assertFalse(moved["digest_match"])
        self.assertEqual(moved["redirect_not_followed"], "https://arquivo.pt/wayback/3id_/y?key=[redacted]")
        self.assertNotIn("body_text", moved)


if __name__ == "__main__":
    unittest.main()
