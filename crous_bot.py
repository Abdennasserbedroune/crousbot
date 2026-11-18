#!/usr/bin/env python3
"""CROUS IdF bot: banner Telegram épinglé (mis à jour en direct), boutons interactifs, alertes."""
import json, os, sys, time, urllib.error, urllib.request
from datetime import datetime
from pathlib import Path

TOOL_ID = int(os.environ.get("CROUS_TOOL_ID", "47"))
POLL_INTERVAL = int(os.environ.get("CROUS_POLL_INTERVAL", "30"))
MAX_RUNTIME = int(os.environ.get("MAX_RUNTIME", "0"))  # secondes, 0 = infini
TG_TOKEN = os.environ["TELEGRAM_TOKEN"].strip()
TG_CHAT = os.environ["TELEGRAM_CHAT_ID"].strip()
STATE_FILE = Path(os.environ.get("STATE_FILE", "state/seen.json"))
IDF = ("75", "77", "78", "91", "92", "93", "94", "95")
API = f"https://trouverunlogement.lescrous.fr/api/fr/search/{TOOL_ID}"

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("Europe/Paris")
except Exception:
    TZ = None

S = {"items": {}, "checks": 0, "last": None, "err": None, "seen": None,
     "banner_id": None, "paused": False, "check_now": False}


def now_str():
    return datetime.now(TZ).strftime("%H:%M:%S")


# ---------------- Telegram ----------------
def tg(method, **p):
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{TG_TOKEN}/{method}",
        json.dumps(p).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=p.get("timeout", 0) + 15) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        if "not modified" not in body:
            print(f"Telegram {method} KO: {e.code} {body[:200]}", flush=True)
        return {"ok": False, "description": body}
    except Exception as e:
        print(f"Telegram {method} KO: {e}", flush=True)
        return {"ok": False, "description": str(e)}


def keyboard():
    return {"inline_keyboard": [
        [{"text": "📊 Statut", "callback_data": "status"},
         {"text": "🏠 Logements", "callback_data": "list"}],
        [{"text": "🔄 Vérifier maintenant", "callback_data": "check"},
         {"text": "▶️ Reprendre" if S["paused"] else "⏸ Pause", "callback_data": "pause"}]]}


def banner_text():
    n = len(S["items"])
    if S["paused"]:
        head = "⏸ CROUS IdF — EN PAUSE"
    elif S["err"]:
        head = "🔴 CROUS IdF — erreur de connexion"
    else:
        head = "🟢 CROUS IdF — surveillance active"
    body = (f"🏠 {n} logement(s) en Île-de-France" if n
            else "😴 Aucun logement disponible pour le moment")
    lines = [head, body, f"🕒 Dernière vérif : {S['last'] or '...'}",
             f"🔁 Check #{S['checks']} · toutes les {POLL_INTERVAL} s"]
    if S["err"]:
        lines.append(f"⚠️ {S['err'][:100]}")
    return "\n".join(lines)


def refresh_banner():
    if S["banner_id"]:
        r = tg("editMessageText", chat_id=TG_CHAT, message_id=S["banner_id"],
               text=banner_text(), reply_markup=keyboard())
        if r.get("ok") or "not modified" in r.get("description", ""):
            return
    r = tg("sendMessage", chat_id=TG_CHAT, text=banner_text(), reply_markup=keyboard())
    if r.get("ok"):
        S["banner_id"] = r["result"]["message_id"]
        tg("pinChatMessage", chat_id=TG_CHAT, message_id=S["banner_id"],
           disable_notification=True)
        save()


def link(acc_id):
    return f"https://trouverunlogement.lescrous.fr/tools/{TOOL_ID}/accommodations/{acc_id}"


def alert_new(i):
    res = i.get("residence", {})
    r = rent(i)
    tg("sendMessage", chat_id=TG_CHAT, text=(
        f"🏠 NOUVEAU LOGEMENT CROUS\n\n{res.get('label','?')}\n{i.get('label','')}\n"
        f"📍 {res.get('address','')}\n💶 {f'{r:.0f} €' if r is not None else '?'}"),
       reply_markup={"inline_keyboard": [[{"text": "🔗 Ouvrir le logement",
                                           "url": link(i["id"])}]]})


def list_text():
    if not S["items"]:
        return "😴 Aucun logement disponible en Île-de-France pour le moment."
    out = [f"🏠 {len(S['items'])} logement(s) :"]
    for i in list(S["items"].values())[:25]:
        r = rent(i)
        out.append(f"• {i.get('residence',{}).get('label','?')} — {i.get('label','')}"
                   f" — {f'{r:.0f} €' if r is not None else '?'}\n  {link(i['id'])}")
    return "\n".join(out)[:4000]


def handle(u):
    cb = u.get("callback_query")
    msg = u.get("message")
    chat = str((cb["message"]["chat"]["id"]) if cb else (msg or {}).get("chat", {}).get("id", ""))
    if chat != TG_CHAT:
        return
    action = cb["data"] if cb else (msg.get("text", "").split("@")[0].lstrip("/").lower())
    toast = None
    if action in ("status", "start"):
        n = len(S["items"])
        toast = (f"✅ {n} logement(s) en IdF" if n else "😴 Aucun logement pour le moment")
        toast += f"\nDernière vérif : {S['last'] or '...'}"
        if not cb:
            refresh_banner()
    elif action in ("list", "logements"):
        tg("sendMessage", chat_id=TG_CHAT, text=list_text())
    elif action in ("check", "verifier"):
        S["check_now"] = True
        toast = "🔄 Vérification en cours..."
    elif action in ("pause", "resume"):
        S["paused"] = not S["paused"]
        save()
        refresh_banner()
        toast = "⏸ Pause" if S["paused"] else "▶️ Surveillance reprise"
    if cb:
        tg("answerCallbackQuery", callback_query_id=cb["id"],
           text=toast or "", show_alert=action == "status")


# ---------------- CROUS ----------------
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
        d = json.loads(STATE_FILE.read_text())
        if isinstance(d, list):
            d = {"seen": d}
        S["seen"] = set(d.get("seen", []))
        S["banner_id"] = d.get("banner_id")
        S["paused"] = d.get("paused", False)
    except Exception:
        pass


def save():
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps({
        "seen": sorted(S["seen"] or []), "banner_id": S["banner_id"], "paused": S["paused"]}))


def check():
    try:
        items = search()
        ids = {i["id"] for i in items}
        if S["seen"] is not None:
            for i in items:
                if i["id"] not in S["seen"]:
                    alert_new(i)
        S["items"] = {i["id"]: i for i in items}
        if S["seen"] != ids:
            S["seen"] = ids
            save()
        S["err"] = None
        print(f"[{now_str()}] OK {len(ids)} logements", flush=True)
    except Exception as e:
        S["err"] = str(e)
        print(f"[{now_str()}] ERREUR: {e}", flush=True)
    S["checks"] += 1
    S["last"] = now_str()
    refresh_banner()


def main():
    start = time.time()
    load()
    print(f"Bot démarré (tool {TOOL_ID}, every {POLL_INTERVAL}s)", flush=True)
    offset, next_check = None, 0
    while True:
        if not S["paused"] and (S["check_now"] or time.time() >= next_check):
            S["check_now"] = False
            check()
            next_check = time.time() + POLL_INTERVAL
        if MAX_RUNTIME and time.time() - start > MAX_RUNTIME:
            print("Fin de session planifiée", flush=True)
            sys.exit(0)
        wait = 25 if S["paused"] else max(1, min(25, int(next_check - time.time())))
        r = tg("getUpdates", timeout=wait, offset=offset,
               allowed_updates=["message", "callback_query"])
        if not r.get("ok"):
            time.sleep(5)
            continue
        for u in r["result"]:
            offset = u["update_id"] + 1
            try:
                handle(u)
            except Exception as e:
                print("handle err:", e, flush=True)


if __name__ == "__main__":
    main()
