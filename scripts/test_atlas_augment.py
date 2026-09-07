import json
import sqlite3
import base64

import atlas_augment as aa


def _html(graph):
    return "<script>\nconst GRAPH = " + json.dumps(graph) + "; const TYPES = {};\n</script>"


def _graph():
    return {
        "nodes": [
            {"id": "page:AgentBase64Test", "label": "AgentBase64Test", "type": "wikipage", "detail": ""},
            {"id": "agent:AgentRelent", "label": "AgentRelent", "type": "agent", "detail": ""},
            {"id": "ip:4.227", "label": "4.227", "type": "ip16", "detail": ""},
            {"id": "end:sec.gov/files/county.json", "label": "sec.gov/files/county.json", "type": "endpoint", "detail": ""},
            {"id": "proxy:jqp", "label": "jqp", "type": "proxy", "detail": ""},
            {"id": "site:vanderbi.lt", "label": "vanderbi.lt", "type": "platform", "detail": ""},
            {"id": "short:vanderbi.lt/abc", "label": "vanderbi.lt/abc", "type": "shortener", "detail": ""},
            {"id": "wiki:dse", "label": "dse", "type": "wiki", "detail": ""},
        ],
        "links": [
            {"source": "agent:AgentRelent", "target": "page:AgentBase64Test", "rel": "edits", "rt": "edits"},
        ],
        "relmeta": {"edits": "edits"},
    }


def test_load_and_dump_roundtrip_preserves_surroundings():
    g = _graph()
    html = _html(g)
    graph, st, end = aa.load_graph(html)
    assert graph == g
    out = aa.dump_graph(html, graph, st, end)
    assert out.startswith("<script>\nconst GRAPH = {") and out.endswith("; const TYPES = {};\n</script>")
    assert aa.load_graph(out)[0] == g


def test_load_graph_handles_braces_inside_strings():
    g = _graph()
    g["nodes"][0]["detail"] = 'has "} and { inside'
    graph, _, _ = aa.load_graph(_html(g))
    assert graph["nodes"][0]["detail"] == 'has "} and { inside'


def test_stamp_times_from_export(tmp_path):
    rows = [
        {"page_id": "dse/AgentBase64Test", "label": "AgentRelent", "ip16": "4.227", "time": "2026-06-16T10:00:00Z"},
        {"page_id": "dse/AgentBase64Test", "label": "AgentRelent", "ip16": "4.227", "time": "2026-05-27T12:42:55Z"},
        {"page_id": "dse/Other", "label": "", "ip16": "4.227", "time": "2026-06-22T08:00:00Z"},
    ]
    (tmp_path / "revisions.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    g = _graph()
    n = aa.stamp_times(g, aa.export_times(tmp_path))
    assert n == 3
    by = {x["id"]: x for x in g["nodes"]}
    assert by["page:AgentBase64Test"]["first"] == "2026-05-27T12:42:55Z"
    assert by["agent:AgentRelent"]["last"] == "2026-06-16T10:00:00Z"
    assert by["ip:4.227"]["last"] == "2026-06-22T08:00:00Z"
    assert "first" not in by["wiki:dse"]


def _pack(tmp_path, body):
    con = sqlite3.connect(tmp_path / "agent-text.sqlite")
    con.execute("CREATE TABLE documents (id TEXT, source_type TEXT, source_group TEXT, timestamp_utc TEXT, text TEXT)")
    con.execute("INSERT INTO documents VALUES ('d1','wiki','dse/AgentBase64Test','2026-05-27T12:42:55Z',?)", (body,))
    con.execute("INSERT INTO documents VALUES ('d2','wiki','dse/NotInAtlas','2026-05-27T12:42:55Z','https://jqp.vercel.app/x')")
    con.execute("INSERT INTO documents VALUES ('d3','wiki','probier/ProbeOnly','2026-05-28T00:00:00Z',"
                "'https://httpbin.org/base64/aHR0cHM6Ly9leGFtcGxlLm9yZy9k')")
    con.commit()
    con.close()
    return tmp_path


def test_pack_edges_cites_known_nodes_and_decodes_base64(tmp_path):
    payload = base64.b64encode(b'<a href="https://api.usaspending.gov/api/v2/x/">t</a>').decode().rstrip("=")
    body = ("see https://jqp.vercel.app/api?url=https://www.sec.gov/files/county.json "
            "and https://www.sec.gov/files/county.json plus https://vanderbi.lt/abc "
            "and https://vanderbi.lt/zzz and https://httpbin.org/base64/" + payload)
    g = _graph()
    stats = aa.pack_edges(g, _pack(tmp_path, body))
    links = {(l["source"], l["target"], l["rt"]) for l in g["links"]}
    assert ("page:AgentBase64Test", "proxy:jqp", "cites") in links
    assert ("page:AgentBase64Test", "short:vanderbi.lt/abc", "cites") in links
    assert ("page:AgentBase64Test", "site:vanderbi.lt", "cites") in links
    assert ("page:AgentBase64Test", "proxy:httpbin-base64", "cites") in links
    assert ("proxy:httpbin-base64", "end:api.usaspending.gov/api/v2/x", "proxies") in links
    # www.sec.gov is not sec.gov: host match is exact, so the endpoint is not cited
    assert ("page:AgentBase64Test", "end:sec.gov/files/county.json", "cites") not in links
    assert all(l.get("src") == "pack" for l in g["links"][1:])
    assert stats["body_revisions"] == 2 and stats["base64_decodes"] == 2 and stats["pages_new"] == 1
    assert "page:NotInAtlas" not in {n["id"] for n in g["nodes"]}   # plain URLs do not add pages
    assert ("page:ProbeOnly", "proxy:httpbin-base64", "cites") in links
    assert ("proxy:httpbin-base64", "end:example.org/d", "proxies") in links
    # idempotent
    again = aa.pack_edges(g, tmp_path)
    assert again.get("cites", 0) == 0 and again.get("base64_decodes", 0) == 0


def test_termina_edges_map_by_alias_host_or_new_node(tmp_path):
    venues = [
        {"id": "dse", "host": "wikiservice.at", "path": "/dse", "kind": "wiki", "status": "live"},
        {"id": "jqp", "host": "jqp.vercel.app", "path": "", "kind": "url-as-storage", "status": "live"},
        {"id": "sec", "host": "sec.gov", "path": "", "kind": "data-target", "status": "live"},
        {"id": "paste-k4be", "host": "pastebin.k4be.pl", "path": "", "kind": "paste", "status": "live"},
        {"id": "wiki4d", "host": "prowiki.org", "path": "/wiki4d", "kind": "wiki", "status": "live"},
    ]
    links = [
        {"from_venue": "jqp", "to_venue": "sec", "kind": "infrastructure", "label": "proxies [corpus]", "evidence_id": "e1"},
        {"from_venue": "paste-k4be", "to_venue": "dse", "kind": "overlap", "label": "match: same object [shellac]", "evidence_id": "e1"},
        {"from_venue": "wiki4d", "to_venue": "dse", "kind": "infrastructure", "label": "contains [known]", "evidence_id": "e1"},
        {"from_venue": "dse", "to_venue": "ghost", "kind": "later", "label": "x", "evidence_id": None},
    ]
    (tmp_path / "venue.jsonl").write_text("\n".join(json.dumps(v) for v in venues) + "\n")
    (tmp_path / "venue_link.jsonl").write_text("\n".join(json.dumps(l) for l in links) + "\n")
    g = _graph()
    stats = aa.termina_edges(g, tmp_path)
    by = {n["id"]: n for n in g["nodes"]}
    assert "paste:paste-k4be" in by and by["paste:paste-k4be"]["type"] == "paste"
    assert "wiki:wiki4d" in by and by["wiki:wiki4d"]["label"] == "prowiki.org/wiki4d"
    L = {(l["source"], l["target"]): l for l in g["links"]}
    assert L[("proxy:jqp", "end:sec.gov/files/county.json")]["rt"] == "proxies"   # host-matched existing endpoint
    assert L[("paste:paste-k4be", "wiki:dse")]["rt"] == "overlap"
    assert L[("wiki:wiki4d", "wiki:dse")]["rt"] == "hosts"
    assert all(l["src"] == "termina" and l["evidence"] == "e1" for k, l in L.items() if k[0] != "agent:AgentRelent")
    assert stats["links"] == 3 and stats["links_unmapped"] == 1 and stats["venues_new"] == 2
    assert g["relmeta"]["overlap"] == "shares identifier"
