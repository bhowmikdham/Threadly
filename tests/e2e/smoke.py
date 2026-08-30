import httpx, json, os, sys

BASE = "http://localhost:8000"
c = httpx.Client(timeout=60)

def sse_events(text):
    events, ev = [], None
    for line in text.splitlines():
        if line.startswith("event: "): ev = line[7:]
        elif line.startswith("data: "): events.append((ev, json.loads(line[6:])))
    return events

def chat(msg, headers=None, **kw):
    with c.stream("POST", f"{BASE}/v1/chat", json={"message": msg, **kw}, headers={**H, **(headers or {})}) as r:
        assert r.status_code == 200, r.status_code
        return sse_events(r.read().decode())

fails = []
def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))
    if not cond: fails.append(name)

# --- auth + sync ---
r = c.post(f"{BASE}/v1/auth/dev", json={"email": "me@example.com"})
check("dev login", r.status_code == 200, r.text)
H = {"Authorization": f"Bearer {r.json()['access_token']}"}
check("401 without token", c.get(f"{BASE}/v1/threads").status_code == 401)
r = c.get(f"{BASE}/v1/auth/google/url")
check("oauth url unconfigured -> 501", r.status_code == 501 and r.json()["error"]["code"] == "google_not_configured", r.text)
r = c.post(f"{BASE}/v1/sync", headers=H)
check("sync", r.status_code == 200 and r.json()["results"][0]["messages"] > 0, r.text)

# --- request id propagation gateway -> orchestrator meta ---
evs = chat("hello there", headers={"X-Request-ID": "smoke-rid-1"})
check("request id propagated", dict(evs)["meta"]["request_id"] == "smoke-rid-1", dict(evs)["meta"])

# --- tier-2 residue path: transactional regex-missed message synced cleanly ---
subjects = [t["subject"] for t in c.get(f"{BASE}/v1/threads", headers=H).json()["items"]]
check("tier-2 candidate synced", any(s and "Jetstar" in s for s in subjects), subjects)

# --- per-message labels (AI-team classifiers; rule fallbacks in dev) ---
r = c.get(f"{BASE}/v1/messages", params={"priority": "high"}, headers=H)
items = r.json()["items"]
check("priority inbox", r.status_code == 200 and any("Q3" in (i["subject"] or "") for i in items), r.text)
check("labels stored with detail", items and all(i["labels"].get("priority", {}).get("source") for i in items))
r = c.get(f"{BASE}/v1/messages", params={"action": "reply"}, headers=H)
check("action filter", any("Q3" in (i["subject"] or "") for i in r.json()["items"]), r.text)
r = c.get(f"{BASE}/v1/messages", params={"category": "purchases"}, headers=H)
check("category filter", len(r.json()["items"]) >= 1, r.text)

# --- GYG entity flow ---
evs = chat("give me the order number of all the orders that i did for GYG")
meta, ent = dict(evs)["meta"], dict(evs).get("entities", {})
check("GYG intent", meta["intent"] == "FETCH_ENTITY" and meta["params"].get("merchant") == "GYG", meta)
keys = [i["key"] for i in ent.get("items", [])]
check("GYG orders (60d window)", len(keys) == 4 and all(k.startswith("GYG-") for k in keys), keys)
check("HTML-only order extracted", "GYG-84990" in keys, keys)
check("has_more + cursor", ent.get("has_more") is True and ent.get("cursor"))

# merchant vocabulary: full brand name must hit the domain-derived merchant
evs2 = chat("show me all my orders from guzman y gomez")
alias_keys = [i["key"] for i in dict(evs2).get("entities", {}).get("items", [])]
check("merchant alias guzman y gomez -> GYG", sorted(alias_keys) == sorted(keys), alias_keys)

# typo tolerance: GIG is one edit from GYG; strict match finds nothing, the
# edit-distance tier kicks in and the response says so via fuzzy=true
evs3 = chat("get me the invoice for GIG")
ent3 = dict(evs3).get("entities", {})
check("typo GIG -> GYG via fuzzy tier",
      ent3.get("fuzzy") is True and len(ent3.get("items", [])) >= 1
      and all(i["merchant"] == "GYG" for i in ent3["items"]), ent3)
r = c.get(f"{BASE}/v1/entities", params={"type": "amount", "merchant": "GIG"}, headers=H)
check("REST fuzzy flag", r.json().get("fuzzy") is True and len(r.json()["items"]) >= 1, r.text)
r = c.get(f"{BASE}/v1/entities", params={"type": "order", "merchant": "GYG", "cursor": ent["cursor"]}, headers=H)
check("cursor pages older", len(r.json()["items"]) == 2 and r.json()["has_more"] is False, r.text)

# --- commitments ---
evs = chat("what did I promise people this week?")
ent = dict(evs).get("entities", {})
dirs = [i["direction"] for i in ent.get("items", [])]
check("commitments found", dirs.count("inbound") == 2 and dirs.count("outbound") == 1, dirs)
r = c.get(f"{BASE}/v1/commitments", params={"direction": "outbound"}, headers=H)
cid = r.json()["items"][0]["id"]
r = c.post(f"{BASE}/v1/commitments/{cid}/done", headers=H)
check("mark done", r.status_code == 200 and r.json()["status"] == "done", r.text)

# --- voice stubs ---
r = c.post(f"{BASE}/v1/voice/tts", json={"text": "hello"}, headers=H)
check("tts stub", r.status_code == 200 and r.headers["content-type"].startswith("audio/wav") and len(r.content) > 100)
r = c.post(f"{BASE}/v1/voice/stt", files={"audio": ("a.wav", b"RIFF0000WAVE", "audio/wav")}, headers=H)
check("stt stub", r.status_code == 200 and r.json().get("stub") is True and r.json()["text"], r.text)

# --- non-stream generate (machine callers like tier-2) ---
r = c.post("http://inference:8020/v1/generate",
           json={"prompt": "say hi", "stream": False, "max_tokens": 20},
           headers={"X-Threadly-Internal": os.environ["THREADLY_INTERNAL_TOKEN"]})
check("non-stream generate", r.status_code == 200 and r.json()["text"] and r.json()["backend"] == "stub", r.text)

# --- draft lifecycle ---
evs = chat("draft a reply to Priya about the Q3 numbers")
draft = dict(evs).get("draft", {})
check("draft created", draft.get("status") == "generated" and draft.get("draft_id"), draft)
did = draft["draft_id"]
r = c.post(f"{BASE}/v1/drafts/{did}/send", headers=H)
check("send before approve -> 409", r.status_code == 409 and r.json()["error"]["code"] == "illegal_transition", r.text)
c.patch(f"{BASE}/v1/drafts/{did}", json={"to_addrs": ["priya@acme-corp.com"]}, headers=H)
c.post(f"{BASE}/v1/drafts/{did}/approve", headers=H)
r = c.post(f"{BASE}/v1/drafts/{did}/send", headers={**H, "Idempotency-Key": "smoke-1"})
check("send", r.status_code == 200 and r.json()["status"] == "sent", r.text)
gid = r.json()["gmail_msg_id"]
r = c.post(f"{BASE}/v1/drafts/{did}/send", headers={**H, "Idempotency-Key": "smoke-1"})
check("idempotent replay", r.status_code == 200 and r.json()["gmail_msg_id"] == gid, r.text)

# --- summary cache + search ---
boss = next(t for t in c.get(f"{BASE}/v1/threads", headers=H).json()["items"] if t["subject"] and "Q3" in t["subject"])
s1 = dict(chat("summarise this thread", thread_id=boss["id"])).get("summary", {})
check("summary generated", bool(s1.get("text")) and s1.get("cached") is False, s1)
s2 = dict(chat("summarise this thread", thread_id=boss["id"])).get("summary", {})
check("summary cache hit", s2.get("cached") is True and s2.get("text") == s1.get("text"))
res = dict(chat("when is the Q3 report due?")).get("results", {})
check("search hits", res.get("count", 0) >= 1, res)

# --- rate limiting (separate user so main flow stays clean) ---
r = c.post(f"{BASE}/v1/auth/dev", json={"email": "ratelimit@example.com"})
H2 = {"Authorization": f"Bearer {r.json()['access_token']}"}
codes = []
for _ in range(31):
    with c.stream("POST", f"{BASE}/v1/chat", json={"message": "tl;dr"}, headers=H2) as rr:
        codes.append(rr.status_code)
        rr.read()
check("rate limit: 30 ok then 429", codes[:30] == [200] * 30 and codes[30] == 429, codes[-3:])

print("\n" + ("ALL PASS" if not fails else f"FAILURES: {fails}"))
sys.exit(1 if fails else 0)
