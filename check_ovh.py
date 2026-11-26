"""Checks whether OVH has VPS with Linux in stock in a datacenter (default: Warsaw).

Uses the public OVH API, no account or key needed.

Local:    python check_ovh.py [--webhook URL]
          checks every few minutes; when found: Discord alert, beep, opens the OVH page, popup.
CI:       python check_ovh.py --state state.json
          one check; keeps one Discord status message up to date and sends an alert
          (3x @everyone for Warsaw) only when a plan newly comes in stock.
Webhook:  --webhook URL or env var OVH_DISCORD_WEBHOOK
Phone:    env var NTFY_TOPIC: urgent push via the ntfy app when Warsaw gets stock (wakes you up)
"""
import argparse
import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

API = "https://eu.api.ovh.com/v1/vps/order/rule/datacenter?ovhSubsidiary=PL&planCode={}"
ORDER_PAGE = "https://www.ovhcloud.com/pl/vps/"
NAMES = {"vps-2027-model1": "VPS-1", "vps-2027-model2": "VPS-2", "vps-2027-model3": "VPS-3", "vps-2027-model4": "VPS-4"}
PLACES = {"WAW": "Warszawa 🇵🇱", "GRA": "Gravelines 🇫🇷", "SBG": "Strasburg 🇫🇷", "BHS": "Beauharnois 🇨🇦", "DE": "Frankfurt 🇩🇪", "UK": "Londyn 🇬🇧"}
LABELS = {"available": "🟢 dostępny", "out-of-stock": "🔴 brak", "not offered": "⚫ nie ma tu tego planu", "error": "⚠️ błąd API"}
GREEN, RED = 0x2ECC71, 0xE74C3C
# Discord (Cloudflare) rejects Python's default User-Agent.
HEADERS = {"User-Agent": "ovh-vps-check/1.0", "Content-Type": "application/json"}
EVERYONE = {"parse": ["everyone"]}


def linux_status(plan, datacenter):
    with urllib.request.urlopen(urllib.request.Request(API.format(plan), headers=HEADERS), timeout=20) as r:
        data = json.load(r)
    for dc in data["datacenters"]:
        if dc["datacenter"] == datacenter:
            return dc["linuxStatus"]
    return "not offered"


def discord(webhook, payload, message_id=None):
    """POST a new message (returns it), or PATCH an existing one when message_id is given."""
    url = f"{webhook}/messages/{message_id}" if message_id else f"{webhook}?wait=true"
    req = urllib.request.Request(url, json.dumps(payload).encode(), HEADERS, method="PATCH" if message_id else "POST")
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def ntfy(topic, dc, names):
    """Max-priority push to the ntfy phone app; it can ring through Do Not Disturb."""
    body = {"topic": topic, "title": f"OVH: wolny VPS ({dc})!", "message": f"{', '.join(names)} – zamawiaj teraz",
            "priority": 5, "tags": ["rotating_light"], "click": ORDER_PAGE}
    req = urllib.request.Request("https://ntfy.sh/", json.dumps(body).encode(), HEADERS)
    urllib.request.urlopen(req, timeout=20).close()


def status_payload(dc, statuses):
    place = PLACES.get(dc, dc)
    free = "available" in statuses.values()
    return {
        "embeds": [{
            "title": f"🟢 Jest wolny VPS – {place}!" if free else f"🔴 Brak wolnych VPS – {place}",
            "url": ORDER_PAGE,
            "color": GREEN if free else RED,
            "fields": [{"name": n, "value": LABELS.get(s, s), "inline": True} for n, s in statuses.items()],
            "footer": {"text": "Linux · ostatnie sprawdzenie"},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }]
    }


def alert_payload(dc, names, ping):
    payload = {
        "allowed_mentions": EVERYONE,
        "embeds": [{
            "title": f"🚨 Wolny VPS z Linuxem: {', '.join(names)}",
            "description": f"📍 **{PLACES.get(dc, dc)}**\n\n[➡️ Zamów teraz na OVH]({ORDER_PAGE})\nWolne maszyny szybko znikają!",
            "url": ORDER_PAGE,
            "color": GREEN,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }],
    }
    if ping:
        payload["content"] = "@everyone"
    return payload


def check(plans, dc):
    statuses = {}
    for plan in plans:
        name = NAMES.get(plan, plan)
        try:
            statuses[name] = linux_status(plan, dc)
        except Exception as e:  # network hiccup: report and try again next round
            print(f"{datetime.now():%H:%M:%S}  {name:<6} error: {e}", flush=True)
            statuses[name] = "error"
            continue
        print(f"{datetime.now():%H:%M:%S}  {name:<6} {dc}  Linux: {statuses[name]}", flush=True)
    return statuses


def run_ci(a):
    st = json.load(open(a.state)) if os.path.exists(a.state) else {}
    prev, msg_id = st.get("available", []), st.get("status_msg")
    statuses = check(a.plans, a.dc)
    found = [n for n, s in statuses.items() if s == "available"]
    new = [n for n in found if n not in prev]
    found += [n for n, s in statuses.items() if s == "error" and n in prev]  # an API hiccup must not re-alert

    ping = a.ping or a.dc == "WAW"  # @everyone and phone only for the Polish datacenter (or a ping test)
    if new and ping and a.ntfy:
        ntfy(a.ntfy, a.dc, new)  # first, so a failure stops the run and the next run retries the whole alert
        print("ntfy: phone alert sent", flush=True)
    if a.webhook:
        if new:
            discord(a.webhook, alert_payload(a.dc, new, ping))
            for _ in range(2 if ping else 0):  # 3 pings in total
                time.sleep(1)
                discord(a.webhook, {"content": "@everyone 🚨", "allowed_mentions": EVERYONE})
            print("Discord: alert sent", flush=True)
        if msg_id:
            try:
                discord(a.webhook, status_payload(a.dc, statuses), msg_id)
            except urllib.error.HTTPError as e:
                if e.code != 404:
                    raise
                msg_id = None  # status message was deleted on Discord: post a new one
        if not msg_id:
            msg_id = discord(a.webhook, status_payload(a.dc, statuses))["id"]
        print("Discord: status updated", flush=True)

    # Written only after Discord succeeded, so a failed send is retried next run.
    with open(a.state, "w") as f:
        json.dump({"available": found, "status_msg": msg_id}, f, indent=1)
        f.write("\n")


def run_local(a):
    while True:
        statuses = check(a.plans, a.dc)
        found = [n for n, s in statuses.items() if s == "available"]
        if found:
            if a.webhook:
                try:
                    discord(a.webhook, alert_payload(a.dc, found, a.dc == "WAW"))
                    print("Discord: sent", flush=True)
                except Exception as e:
                    print(f"Discord error: {e}", flush=True)
            import ctypes, webbrowser, winsound  # Windows-only, so not imported in CI

            winsound.Beep(1000, 700)
            webbrowser.open(ORDER_PAGE)
            ctypes.windll.user32.MessageBoxW(None, f"OVH: wolny VPS z Linuxem ({a.dc}): {', '.join(found)}", "OVH VPS", 0x40)
            return
        if a.once:
            return
        time.sleep(a.interval * 60)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--plans", nargs="+", default=["vps-2027-model1", "vps-2027-model2", "vps-2027-model3"])
    p.add_argument("--dc", default="WAW", help="datacenter code, e.g. WAW, GRA, BHS")
    p.add_argument("--interval", type=float, default=5, help="minutes between checks (local mode)")
    p.add_argument("--once", action="store_true", help="check once and exit (local mode)")
    p.add_argument("--webhook", default=os.environ.get("OVH_DISCORD_WEBHOOK"))
    p.add_argument("--ntfy", default=os.environ.get("NTFY_TOPIC"), help="ntfy topic for the phone alarm")
    p.add_argument("--state", help="CI mode: JSON file with last availability and the status message id")
    p.add_argument("--ping", action="store_true", help="CI mode: @everyone even outside Warsaw (for testing)")
    p.add_argument("--phone-test", action="store_true", help="only send a test alarm to the phone (checks the NTFY_TOPIC secret)")
    a = p.parse_args()
    if a.phone_test:
        if not a.ntfy:
            raise SystemExit("NTFY_TOPIC is not set")
        return ntfy(a.ntfy, "TEST", ["Test alarmu"])
    run_ci(a) if a.state else run_local(a)


if __name__ == "__main__":
    main()
