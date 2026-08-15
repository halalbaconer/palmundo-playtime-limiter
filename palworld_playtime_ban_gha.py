#!/usr/bin/env python3
import json
import os
import sys
import logging
from datetime import datetime, date, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from requests.auth import HTTPBasicAuth

LOCAL_TZ = ZoneInfo("Europe/Paris")

# ============================== CONFIG ==============================

SERVER_HOST = os.environ["PAL_SERVER_HOST"]
SERVER_PORT = int(os.environ.get("PAL_SERVER_PORT", "8212"))
ADMIN_PASSWORD = os.environ["PAL_ADMIN_PASSWORD"]
DAILY_LIMIT_HOURS = float(os.environ.get("PAL_DAILY_LIMIT_HOURS", "4"))

STATE_FILE = Path(__file__).with_name("playtime_state.json")

BAN_MESSAGE = f"Quota journalier de {DAILY_LIMIT_HOURS}h atteint. Reviens demain !"

MAX_DELTA_SECONDS = 15 * 60  # 15 minutes

# ======================================================================

BASE_URL = f"http://{SERVER_HOST}:{SERVER_PORT}/v1/api"
AUTH = HTTPBasicAuth("admin", ADMIN_PASSWORD)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("pal-playtime-ban")


def load_state() -> dict:
    if STATE_FILE.exists():
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "date": str(datetime.now(LOCAL_TZ).date()),
        "playtime_seconds": {},
        "banned_today": [],
        "last_check_utc": None,
    }


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def unban_player(steam_id: str) -> None:
    try:
        userid = steam_id if steam_id.startswith("steam_") else f"steam_{steam_id}"
        r = requests.post(f"{BASE_URL}/unban", auth=AUTH, json={"userid": userid}, timeout=10)
        r.raise_for_status()
        log.info("UNBAN appliqué à %s (reset journalier)", steam_id)
    except Exception as e:
        log.error("Échec de l'unban pour %s : %s", steam_id, e)


def ban_player(steam_id: str, name: str = "") -> None:
    try:
        userid = steam_id if steam_id.startswith("steam_") else f"steam_{steam_id}"
        r = requests.post(
            f"{BASE_URL}/ban",
            auth=AUTH,
            json={"userid": userid, "message": BAN_MESSAGE},
            timeout=10,
        )
        r.raise_for_status()
        log.warning("BAN appliqué à %s (%s) - quota journalier dépassé", name or "?", steam_id)
    except Exception as e:
        log.error("Échec du ban pour %s : %s", steam_id, e)


def reset_if_new_day(state: dict) -> dict:
    today = str(datetime.now(LOCAL_TZ).date())
    if state["date"] != today:
        log.info("Nouveau jour détecté (%s) -> reset des compteurs + unban", today)
        for steam_id in state.get("banned_today", []):
            unban_player(steam_id)
        state = {
            "date": today,
            "playtime_seconds": {},
            "banned_today": [],
            "last_check_utc": None,
        }
    return state


def get_online_players() -> list[dict]:
    try:
        r = requests.get(f"{BASE_URL}/players", auth=AUTH, timeout=15)
        r.raise_for_status()
        data = r.json()
        return data.get("players", [])
    except Exception as e:
        log.error("Impossible de récupérer la liste des joueurs : %s", e)
        return []


def main() -> None:
    state = load_state()
    state = reset_if_new_day(state)

    now = datetime.now(timezone.utc)
    last_check = state.get("last_check_utc")
    if last_check:
        delta = (now - datetime.fromisoformat(last_check)).total_seconds()
        delta = max(0, min(delta, MAX_DELTA_SECONDS))
    else:
        delta = 0  # premier run, pas de delta à ajouter

    players = get_online_players()
    limit_seconds = DAILY_LIMIT_HOURS * 3600

    for p in players:
        steam_id = str(p.get("steamId") or p.get("userId", "")).replace("steam_", "")
        name = p.get("name", "?")
        if not steam_id or steam_id in state["banned_today"]:
            continue

        current = state["playtime_seconds"].get(steam_id, 0) + delta
        state["playtime_seconds"][steam_id] = current

        log.info("%s (%s) : %.0f min jouées aujourd'hui / %.0f min limite",
                  name, steam_id, current / 60, limit_seconds / 60)

        if current >= limit_seconds:
            ban_player(steam_id, name)
            state["banned_today"].append(steam_id)

    state["last_check_utc"] = now.isoformat()
    save_state(state)


if __name__ == "__main__":
    main()
