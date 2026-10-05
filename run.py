"""Планировщик постов с точным временем (по Вене, летнее/зимнее время учитывается само).

GitHub запускает этот скрипт каждые 30 минут, но с опозданием на 5–30 минут.
Поэтому скрипт смотрит на ближайший слот из расписания, заранее готовит пост
(новости + ИИ), ждёт до точной минуты и только тогда публикует.

Запуск вручную:  python run.py            — обычный режим по расписанию
                 python run.py news       — опубликовать новость прямо сейчас
                 python run.py summary    — опубликовать сводку прямо сейчас
"""
import json
import subprocess
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import bot
import news

TZ = ZoneInfo("Europe/Vienna")

# ===== РАСПИСАНИЕ (время по Вене) =====
SCHEDULE = {
    "09:00": "summary",
    "10:00": "news",
    "11:30": "news",
    "13:00": "news",
    "14:30": "news",
    "16:00": "news",
    "17:30": "news",
    "19:00": "news",
    "20:30": "news",
    "22:00": "news",
}

EARLY = timedelta(minutes=15)  # насколько заранее слот можно «взять в работу» (будильник — за 10 мин)
LATE = timedelta(minutes=40)   # если GitHub опоздал сильнее — слот пропускаем


def slot_key(dt):
    return dt.strftime("%Y-%m-%d %H:%M")


def find_slot(now, done):
    """Ближайший невыполненный слот в окне [now-LATE, now+EARLY]."""
    candidates = []
    for day in (now.date() - timedelta(days=1), now.date()):
        for hm, kind in SCHEDULE.items():
            h, m = map(int, hm.split(":"))
            at = datetime(day.year, day.month, day.day, h, m, tzinfo=TZ)
            if now - LATE <= at <= now + EARLY and slot_key(at) not in done:
                candidates.append((at, kind))
    return min(candidates) if candidates else None


def wait_until(at):
    delay = (at - datetime.now(TZ)).total_seconds()
    if delay > 0:
        print(f"Жду до {at:%H:%M} ({delay / 60:.1f} мин)…", flush=True)
        time.sleep(delay)


def main():
    state = news.load_state()
    done = state.setdefault("slots_done", [])
    forced = sys.argv[1] if len(sys.argv) > 1 else None

    if forced in ("news", "summary"):
        print(f"Ручной запуск: {forced}")
        run_slot(datetime.now(TZ), forced, state, check=False)
        if not news.DRY_RUN:
            news.save_state(state)
        return

    # Обрабатываем все слоты в окне: сначала пропущенный (если GitHub опоздал),
    # затем ближайший предстоящий — чтобы он вышел ровно вовремя, а не со следующим запуском.
    handled = 0
    while True:
        now = datetime.now(TZ)
        slot = find_slot(now, set(done))
        if not slot:
            break
        at, kind = slot
        print(f"Слот {at:%H:%M} ({kind}), сейчас {now:%H:%M}.", flush=True)
        run_slot(at, kind, state)
        done.append(slot_key(at))
        state["slots_done"] = done[-60:]
        if not news.DRY_RUN:
            news.save_state(state)
        handled += 1
    if not handled:
        print(f"{datetime.now(TZ):%H:%M} по Вене — ближайших слотов нет, выхожу.")


def remote_state():
    """Самая свежая версия posted.json прямо с GitHub (защита от дублей,
    если параллельный запуск уже опубликовал этот слот)."""
    try:
        subprocess.run(["git", "fetch", "-q", "origin", "main"], check=True, timeout=60)
        out = subprocess.run(["git", "show", "origin/main:posted.json"],
                             check=True, capture_output=True, text=True, timeout=30).stdout
        return json.loads(out)
    except Exception as e:
        print("не удалось проверить свежее состояние:", e, file=sys.stderr)
        return None


def already_done(at, state):
    """True, если слот уже выполнен другим запуском. Заодно подтягивает чужие отметки."""
    if news.DRY_RUN:
        return False
    fresh = remote_state()
    if not fresh:
        return False
    for link in fresh.get("posted", []):
        if link not in state["posted"]:
            state["posted"].append(link)
    for s in fresh.get("slots_done", []):
        if s not in state["slots_done"]:
            state["slots_done"].append(s)
    return slot_key(at) in fresh.get("slots_done", [])


def run_slot(at, kind, state, check=True):
    if check and already_done(at, state):
        print(f"Слот {at:%H:%M} уже опубликован другим запуском — пропускаю.")
        return
    if kind == "news":
        ready = news.prepare(state, limit=1)  # ИИ работает заранее, до нужной минуты
        wait_until(at)
        if check and already_done(at, state):  # повторная проверка прямо перед отправкой
            print(f"Слот {at:%H:%M} уже опубликован другим запуском — пропускаю.")
            return
        news.publish(ready)
    else:
        wait_until(at)
        if check and already_done(at, state):
            print(f"Слот {at:%H:%M} уже опубликован другим запуском — пропускаю.")
            return
        bot.main()  # курсы берём в момент публикации, чтобы цифры были свежими


if __name__ == "__main__":
    main()
