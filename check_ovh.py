"""Checks every few minutes whether OVH has VPS with Linux in stock in a datacenter (default: Warsaw).

Uses the public OVH API, no account or key needed. When found: Discord message (if a webhook is set),
beep, opens the OVH VPS page and shows a popup, then exits.

Run:      python check_ovh.py
Discord:  python check_ovh.py --webhook "https://discord.com/api/webhooks/..."
          (or set env var OVH_DISCORD_WEBHOOK so the URL isn't typed in the command line)
CI:       python check_ovh.py --state state.txt
          (one check, Discord only, and only for plans that were not available last run)
"""
import argparse
import json
import os
import time
import urllib.request
from datetime import datetime

API = "https://eu.api.ovh.com/v1/vps/order/rule/datacenter?ovhSubsidiary=PL&planCode={}"
ORDER_PAGE = "https://www.ovhcloud.com/pl/vps/"
NAMES = {"vps-2027-model1": "VPS-1", "vps-2027-model2": "VPS-2", "vps-2027-model3": "VPS-3", "vps-2027-model4": "VPS-4"}
# Discord (Cloudflare) rejects Python's default User-Agent.
HEADERS = {"User-Agent": "ovh-vps-check/1.0", "Content-Type": "application/json"}


def linux_status(plan, datacenter):
    with urllib.request.urlopen(urllib.request.Request(API.format(plan), headers=HEADERS), timeout=20) as r:
        data = json.load(r)
    for dc in data["datacenters"]:
        if dc["datacenter"] == datacenter:
            return dc["linuxStatus"]
    return "not offered"


def send_discord(webhook, msg):
    body = json.dumps({"content": msg}).encode()
    urllib.request.urlopen(urllib.request.Request(webhook, data=body, headers=HEADERS), timeout=20).close()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--plans", nargs="+", default=["vps-2027-model2", "vps-2027-model3"])
    p.add_argument("--dc", default="WAW", help="datacenter code, e.g. WAW, GRA, BHS")
    p.add_argument("--interval", type=float, default=5, help="minutes between checks")
    p.add_argument("--once", action="store_true", help="check once and exit")
    p.add_argument("--webhook", default=os.environ.get("OVH_DISCORD_WEBHOOK"))
    p.add_argument("--state", help="CI mode: file remembering what was available last run")
    a = p.parse_args()

    while True:
        found, errored = [], []
        for plan in a.plans:
            name = NAMES.get(plan, plan)
            try:
                status = linux_status(plan, a.dc)
            except Exception as e:  # network hiccup: report and try again next round
                print(f"{datetime.now():%H:%M:%S}  {name:<6} error: {e}", flush=True)
                errored.append(name)
                continue
            print(f"{datetime.now():%H:%M:%S}  {name:<6} {a.dc}  Linux: {status}", flush=True)
            if status == "available":
                found.append(name)

        if a.state:
            # Runs every few minutes in CI: only notify when a plan newly comes in stock, not on every run.
            prev = open(a.state).read().split() if os.path.exists(a.state) else []
            new = [n for n in found if n not in prev]
            found += [n for n in errored if n in prev]  # an API hiccup must not cause a repeat alert
            with open(a.state, "w") as f:
                f.write("\n".join(found) + "\n")
            if new and a.webhook:
                send_discord(a.webhook, f"OVH ma wolny VPS z Linuxem w {a.dc}: {', '.join(new)} -> {ORDER_PAGE}")
                print("Discord: sent", flush=True)
            return

        if found:
            msg = f"OVH ma wolny VPS z Linuxem w {a.dc}: {', '.join(found)} -> {ORDER_PAGE}"
            if a.webhook:
                try:
                    send_discord(a.webhook, msg)
                    print("Discord: sent", flush=True)
                except Exception as e:
                    print(f"Discord error: {e}", flush=True)
            import ctypes, webbrowser, winsound  # Windows-only, so not imported in CI

            winsound.Beep(1000, 700)
            webbrowser.open(ORDER_PAGE)
            ctypes.windll.user32.MessageBoxW(None, msg, "OVH VPS available", 0x40)
            return
        if a.once:
            return
        time.sleep(a.interval * 60)


if __name__ == "__main__":
    main()
