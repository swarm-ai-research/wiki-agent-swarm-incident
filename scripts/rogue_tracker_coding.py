"""Hand-code Rogue AI Tracker field incidents (test 4, bead q3du).

Source: https://rogueaitracker.com/api/incidents fetched 2026-09-17. Pass the saved JSON path as argv[1];
the tracker data is linked, not re-hosted. Writes our coding (no titles or summaries)
to data/rogue_ai_tracker_coding.csv. Note: analysis/rogue-agent-explosion-tests.md, test 4.
Subset: tags 'Field evidence' or 'Lab-to-field incident' (70 of 140). Coding is ours, from
each record's summary/details; the tracker has no model/weights/hosting/log fields.

weights: open | closed | mixed | unk
ran:     lab (developer's own eval/infra or first-party product) | deployer (user, company,
         researcher running a model) | criminal (attacker-controlled) | unk
evidence: L  agent-side logs/transcripts held and published by the lab or deployer
          R  agent-side logs recovered from exposed attacker infrastructure
          E  effects only: victim-side forensics, sensors, honeypots, public artifacts
          P  press/anecdote without logs or forensics
"""
import csv, json, collections, sys

CODE = {
 "autogpt-independently-installed-node-and-launched-a-ser": ("GPT-4","closed","deployer","P"),
 "donotpay-s-gpt-4-agent-cancelled-services-and": ("GPT-4","closed","deployer","L"),
 "ashley-called-thousands-of-pennsylvania-voters-with-tai": ("unnamed","unk","deployer","P"),
 "butterflies-agents-ran-social-media-accounts-and-intera": ("unnamed","unk","deployer","P"),
 "a-misconfigured-lindy-agent-answered-emails-and-closed": ("unnamed","unk","deployer","P"),
 "payman-s-developer-relations-agent-posted-a-300": ("unnamed","unk","deployer","P"),
 "a-claude-agent-upgraded-a-desktop-and-left": ("Claude","closed","deployer","L"),
 "truth-terminal-promoted-goat-and-accumulated-valuable-c": ("Claude 3 Opus/Llama","mixed","deployer","P"),
 "an-ai-agent-created-a-live-google-account": ("unnamed","unk","deployer","P"),
 "freysa-released-a-50-000-prize-pool-despite": ("unnamed","unk","deployer","L"),
 "openai-operator-bought-eggs-and-hired-delivery-without": ("OpenAI Operator","closed","lab","P"),
 "claudius-hallucinated-a-human-identity-and-contacted-se": ("Claude Sonnet","closed","lab","L"),
 "claude-sonnet-4-bypassed-an-rm-block-with": ("Claude Sonnet 4","closed","deployer","L"),
 "gemini-deleted-a-coding-project-s-files-after": ("Gemini","closed","deployer","P"),
 "project-vend-agents-ran-a-multi-site-vending": ("Claude","closed","lab","L"),
 "forcedleak-salesforce-agentforce": ("Agentforce","unk","deployer","L"),
 "chatgpt-agent-fabricated-an-answer-after-cloudflare-blo": ("ChatGPT Agent","closed","lab","P"),
 "anonymouskit-ai-voice-phishing": ("unnamed voice","unk","criminal","R"),
 "anthropic-claude-cyber-espionage": ("Claude Code","closed","criminal","L"),
 "radware-shadowleak-chatgpt-gmail": ("ChatGPT","closed","lab","L"),
 "gpt-5-changed-working-research-code-without-being": ("GPT-5","closed","deployer","P"),
 "appomni-servicenow-agent-discovery-injection": ("Now Assist","unk","deployer","L"),
 "google-antigravity-deleted-a-user-s-entire-d": ("Gemini/Antigravity","closed","deployer","L"),
 "a-browser-agent-disclosed-confidential-acquisition-disc": ("unnamed","unk","deployer","P"),
 "claude-agents-negotiated-186-deals-and-caused-employees": ("Claude","closed","lab","L"),
 "andon-fm-agent-radio-business": ("Gemini et al.","closed","deployer","L"),
 "an-ai-village-agent-sent-rob-pike-an": ("Claude Opus 4","closed","deployer","L"),
 "anthropic-claude-cyber-eval-real-systems": ("Claude","closed","lab","L"),
 "dj-claude-abandoned-ordinary-programming-for-activism-a": ("Claude Haiku 4","closed","deployer","L"),
 "andon-bengt-hired-human-labor": ("unnamed","unk","deployer","L"),
 "mj-rathbun-researched-and-published-a-retaliatory-hit": ("OpenClaw, model unnamed","unk","unk","E"),
 "mona-made-repeated-unnecessary-purchases-while-autonomo": ("Gemini","closed","deployer","L"),
 "rachel-impersonated-a-customer-while-calling-3-000": ("Claude + ElevenLabs","closed","deployer","L"),
 "trendmicro-gemini-cli-dental-botnet": ("Gemini CLI","closed","criminal","R"),
 "andon-ai-bosses-human-employees": ("unnamed","unk","deployer","L"),
 "aurora-cursor-agent-ransomware-intrusions": ("Claude Sonnet 4.5 (Cursor)","closed","criminal","R"),
 "cursor-pocketos-production-database-deletion": ("Claude Opus 4.6 (Cursor)","closed","deployer","P"),
 "openai-s-long-horizon-model-escaped-its-sandbox": ("OpenAI internal","closed","lab","L"),
 "dn42-ai-agent-aws-overprovisioning": ("unnamed","unk","deployer","E"),
 "sysdig-llm-agent-marimo-post-exploitation": ("unnamed","unk","criminal","E"),
 "andon-cafe-ai-agent-manager": ("Gemini","closed","deployer","P"),
 "openai-attributed-rubygems-agent-swarm": ("OpenAI eval models","closed","lab","E"),
 "openai-huggingface-may-account-hijacking": ("OpenAI eval models","closed","lab","E"),
 "gtig-multi-agent-credential-harvesting": ("unnamed coding chatbot","unk","criminal","E"),
 "grok-bankrbot-wallet-drain": ("Grok/Bankrbot","closed","deployer","P"),
 "openai-attributed-dsewiki-agent-swarm": ("OpenAI eval models","closed","lab","E"),
 "openai-thrive-tax-ai-codex-self-improvement": ("Codex","closed","lab","L"),
 "pillar-adk-agent-to-agent-privilege-boundary": ("Gemini","closed","deployer","L"),
 "pillar-gemini-cli-gcp-triage-compromise": ("Gemini CLI","closed","deployer","L"),
 "wiz-red-agent-snowflake-jira": ("Wiz Red Agent","unk","deployer","L"),
 "meta-muse-spark-irregular-real-website": ("Muse Spark 1.1","closed","lab","L"),
 "dream-multi-agent-government-asia": ("Hermes/OpenClaw, model unnamed","unk","criminal","R"),
 "sysdig-jadepuffer-agentic-ransomware": ("unnamed","unk","criminal","E"),
 "jesta-darkreasoning-deepseek-proxyjacking": ("DeepSeek V4 Flash","open","criminal","E"),
 "russian-ai-drone-zaporizhzhia-gas-station": ("onboard targeting (Jetson)","unk","criminal","E"),
 "andon-fm-six-weeks-later": ("Claude/Gemini/GPT/Grok","closed","deployer","L"),
 "openai-agent-collective-blackhat": ("OpenAI eval models","closed","lab","L"),
 "huggingface-ai-agent-production-intrusion": ("GPT-5.6","closed","lab","L"),
 "anthropic-unmonitored-claude-cluster-jobs": ("Claude","closed","lab","L"),
 "gentlemen-hermes-agent-extortion": ("DeepSeek-V4-Pro (Hermes)","open","criminal","R"),
 "secflow-fengtai-government-campaign": ("Claude/Qwen/DeepSeek","mixed","criminal","R"),
 "aisi-unsanctioned-agent-cyber-testing": ("GPT-5.6/Claude","closed","lab","L"),
 "coding-agent-georgia-ballot-reconstruction": ("unnamed public coding agent","unk","deployer","L"),
 "tenet-ghostjacking-agentic-kill-chain": ("Claude Code","closed","deployer","L"),
 "adversa-grok-cryptographic-context-injection": ("Grok","closed","lab","L"),
 "instinct-unauthorized-email": ("unnamed","unk","deployer","P"),
 "claude-fable-home-directory-deletion": ("Claude","closed","deployer","L"),
 "anthropic-mhs-lab-quantum-hardware": ("Claude","closed","lab","L"),
 "papercut-ai-agent-global-intrusion": ("DeepSeek via Codex harness","open","criminal","E"),
 "unit42-ai-assisted-enterprise-intrusion": ("unnamed frontier models","unk","criminal","E"),
}

R = json.load(open(sys.argv[1]))["incidents"]
F = [r for r in R if "Field evidence" in r["tags"] or "Lab-to-field incident" in r["tags"]]
rows = []
for r in F:
    key = next(k for k in CODE if r["slug"].startswith(k))
    m, w, ran, ev = CODE[key]
    date = (r.get("occurredAt") or r.get("publishedAt"))[:10]
    rows.append(dict(date=date, published=(r.get("publishedAt") or "")[:10], slug=r["slug"], attribution=r["evidenceAttribution"], model=m, weights=w, ran=ran, evidence=ev))
assert len(rows) == len(CODE) == 70
rows.sort(key=lambda x: x["date"])
with open("data/rogue_ai_tracker_coding.csv", "w", newline="") as f:
    wr = csv.DictWriter(f, fieldnames=list(rows[0])); wr.writeheader(); wr.writerows(rows)

def summ(name, rs):
    n = len(rs); c = collections.Counter
    logs = sum(x["evidence"] in "LR" for x in rs)
    opn = sum(x["weights"] in ("open", "mixed") for x in rs)
    known = sum(x["weights"] != "unk" for x in rs)
    print(f"| {name} | {n} | {logs}/{n} ({logs/n:.0%}) | {dict(sorted(c(x['evidence'] for x in rs).items()))} | "
          f"{opn}/{n} ({opn/n:.0%}); {opn}/{known} known | {dict(sorted(c(x['ran'] for x in rs).items()))} |")

print("| slice | n | agent-side logs (L+R) | evidence mix | open/mixed weights | where ran |\n|---|---|---|---|---|---|")
half = len(rows) // 2
summ(f"all, first half (≤{rows[half-1]['date']})", rows[:half])
summ(f"all, second half (≥{rows[half]['date']})", rows[half:])
for lo, hi, lab in [("0000", "2026-01-01", "≤2025"), ("2026-01-01", "2026-07-01", "2026 H1"), ("2026-07-01", "9999", "2026-07..09")]:
    summ(lab, [x for x in rows if lo <= x["date"] < hi])
crim = [x for x in rows if x["ran"] == "criminal"]
h = len(crim) // 2
summ(f"criminal-run, first half (≤{crim[h-1]['date']})", crim[:h])
summ(f"criminal-run, second half", crim[h:])
