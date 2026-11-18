#!/usr/bin/env python3
"""CROUS IdF bot headless: poll toutes les N secondes, alerte Telegram sur nouveau logement."""
import json, os, sys, time, urllib.parse, urllib.request
from pathlib import Path

TOOL_ID = int(os.environ.get("CROUS_TOOL_ID", "47"))
POLL_INTERVAL = int(os.environ.get("CROUS_POLL_INTERVAL", "30"))
MAX_RUNTIME = int(os.environ.get("MAX_RUNTIME", "0"))  # secondes, 0 = infini
TG_TOKEN = os.environ["TELEGRAM_TOKEN"]
TG_CHAT = os.environ["TELEGRAM_CHAT_ID"]
STATE_FILE = Path(os.environ.get("STATE_FILE", "state/seen.json"))
IDF = ("75", "77", "78", "91", "92", "93", "94", "95")
API = f"https://trouverunlogement.lescrous.fr/api/fr/search/{TOOL_ID}"


def telegram(text):
    data = urllib.parse.urlencode({"chat_id": TG_CHAT, "text": text,
                                   "disable_web_page_preview": "true"}).encode()
    try:
        urllib.request.urlopen(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage", data, timeout=15)
    except Exception as e:
        print("Telegram KO:", e, flush=True)


def is_idf(acc):
    for p in acc.get("residence", {}).get("address", "").split():
        if len(p) == 5 and p.isdigit():
            return p[:2] in IDF
    return False


def search():
    payload = json.dumps({
        "idTool": TOOL_ID, "need_aggregation": True, "page": 1, "pageSize": 200,
        "sector": None, "occupationModes": ["alone", "house_sharing", "couple"],
        "location": None, "residence": None, "precision": None, "equipment": None,
        "price": {"max": 1000000}, "area": {"min": 0}, "adaptedPmr": False,
    }).encode()
    req = urllib.request.Request(API, payload, method="POST", headers={
        "Content-Type": "application/json", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = json.loads(r.read())
    if "errors" in data:
        raise RuntimeError(data["errors"])
    return [i for i in data.get("results", {}).get("items", []) if is_idf(i)]


def rent(acc):
    for m in acc.get("occupationModes", []):
        if m.get("type") == "alone":
            return m.get("rent", {}).get("min", 0) / 100
    return None


def load():
    try:
        return set(json.loads(STATE_FILE.read_text()))
    except Exception:
        return None  # None = pas de baseline


def save(seen):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(sorted(seen)))


def main():
    start = time.time()
    seen = load()
    errors = 0
    print(f"Bot démarré (tool {TOOL_ID}, every {POLL_INTERVAL}s)", flush=True)
    while True:
        try:
            items = search()
            errors = 0
            ids = {i["id"] for i in items}
            if seen is None:
                telegram(f"✅ Bot CROUS IdF actif - {len(ids)} logement(s) actuellement.")
            else:
                for i in items:
                    if i["id"] not in seen:
                        res = i.get("residence", {})
                        r = rent(i)
                        telegram(
                            f"🏠 NOUVEAU LOGEMENT CROUS\n{res.get('label','?')}\n"
                            f"{i.get('label','')}\n{res.get('address','')}\n"
                            f"Loyer: {r if r is not None else '?'} €\n"
                            f"https://trouverunlogement.lescrous.fr/tools/{TOOL_ID}/accommodations/{i['id']}")
            if seen is None or ids != seen:
                save(ids)
            seen = ids
            print(time.strftime("[%H:%M:%S]"), f"OK {len(ids)} logements", flush=True)
        except Exception as e:
            errors += 1
            print(time.strftime("[%H:%M:%S]"), "ERREUR:", e, flush=True)
            if errors == 10:
                telegram(f"⚠️ Bot CROUS: 10 erreurs d'affilée ({str(e)[:100]})")
        if MAX_RUNTIME and time.time() - start > MAX_RUNTIME:
            print("Fin de session planifiée", flush=True)
            sys.exit(0)
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
