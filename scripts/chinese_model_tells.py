#!/usr/bin/env python3
"""Scan export bodies for Chinese-model authorship tells.

Reads the collusion.wiki revisions export (JSONL, optionally gzipped) and counts,
per revision, wiki and ISO week, the surface signals a Chinese-lab model would be
expected to leave: CJK ideographs, full-width punctuation, corner brackets, kana,
hangul, Cyrillic, Chinese model / vendor names, Chinese cloud or .cn hosts, and
stock Chinese assistant phrases. Bodies whose non-ASCII content is UTF-8 read as
Latin-1 (the export's ``utf8`` rows) are repaired before matching, so mojibake
cannot hide a hit. Every hit is listed with its revision id so it can be read in
context; the JSON is body-free.

  python3 scripts/chinese_model_tells.py --export revisions.jsonl.gz --out data/chinese_model_tells.json
"""
import argparse, collections, gzip, json, re
from datetime import datetime

TELLS = {
    "cjk": re.compile(r"[一-鿿㐀-䶿]"),
    "fullwidth_punct": re.compile(r"[，。：；！？（）【】、]"),
    "corner_brackets": re.compile(r"[「-』]"),
    "kana": re.compile(r"[぀-ヿ]"),
    "hangul": re.compile(r"[가-힯]"),
    "cyrillic": re.compile(r"[Ѐ-ӿ]"),
    "model_or_vendor_name": re.compile(
        r"(GLM|ChatGLM|Zhipu|智谱|DeepSeek|Qwen|Tongyi|通义|Kimi|Moonshot|ERNIE|文心|Doubao|豆包|"
        r"MiniMax|Baichuan|Hunyuan|Volcengine|bigmodel\.cn|dashscope|siliconflow)", re.I),
    "cn_host": re.compile(
        r"https?://[^\s/\"<>]*?(\.cn(?:/|\b)|aliyun|alibabacloud|tencent|qq\.com|baidu|huawei|volces|"
        r"bytedance|zhipu|bigmodel|deepseek|moonshot|weixin|gitee|ubuntu\.org\.cn)[^\s\"<>]*", re.I),
    "stock_phrase": re.compile(r"(作为一个|人工智能助手|抱歉|我无法|请注意|以下是|智能体|截止时间)"),
}


def repair(body):
    """Undo UTF-8-as-Latin-1 mojibake; return (text, repaired?)."""
    if not any(ord(c) > 127 for c in body):
        return body, False
    try:
        return body.encode("latin-1").decode("utf-8"), True
    except (UnicodeEncodeError, UnicodeDecodeError):
        return body, False


def iter_rows(path):
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def scan(path, max_examples=8):
    counts = collections.Counter()
    by_wiki = collections.defaultdict(collections.Counter)
    by_week = collections.defaultdict(collections.Counter)
    examples = collections.defaultdict(list)
    tokens = collections.defaultdict(collections.Counter)
    n = repaired = nonascii = 0
    labels_cjk, pages_cjk = set(), set()
    for r in iter_rows(path):
        n += 1
        body, fixed = repair(r.get("body") or "")
        repaired += fixed
        nonascii += any(ord(c) > 127 for c in body)
        try:
            wk = datetime.fromisoformat(r["time"].replace("Z", "+00:00")).strftime("%G-W%V")
        except (KeyError, ValueError):
            wk = "unknown"
        if TELLS["cjk"].search(r.get("label") or ""):
            labels_cjk.add(r["label"])
        if TELLS["cjk"].search(r.get("name") or ""):
            pages_cjk.add(r.get("page_id"))
        for tell, rx in TELLS.items():
            found = rx.findall(body)
            if not found:
                continue
            counts[tell] += 1
            by_wiki[r.get("wiki")][tell] += 1
            by_week[wk][tell] += 1
            if tell in ("model_or_vendor_name", "cn_host"):
                tokens[tell].update(x.lower() for x in found)
            if len(examples[tell]) < max_examples:
                m = rx.search(body)
                ctx = body[max(0, m.start() - 60): m.end() + 60]
                examples[tell].append({"rev_id": r.get("rev_id"), "label": r.get("label"),
                                       "time": r.get("time"), "context": re.sub(r"\s+", " ", ctx)})
    return {
        "revisions": n, "bodies_repaired_from_mojibake": repaired, "bodies_with_non_ascii_after_repair": nonascii,
        "labels_with_cjk": sorted(labels_cjk), "pages_with_cjk": sorted(p for p in pages_cjk if p),
        "revisions_with_tell": dict(counts),
        "by_wiki": {w: dict(c) for w, c in sorted(by_wiki.items())},
        "by_week": {w: dict(c) for w, c in sorted(by_week.items())},
        "tokens": {k: dict(v.most_common()) for k, v in tokens.items()},
        "examples": dict(examples),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--export", required=True, help="revisions.jsonl or .jsonl.gz")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    res = scan(a.export)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    print(json.dumps({k: res[k] for k in ("revisions", "bodies_repaired_from_mojibake", "revisions_with_tell", "tokens")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
