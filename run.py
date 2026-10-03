"""Планировщик постов с точным временем (по Вене, летнее/зимнее время учитывается само).

GitHub запускает этот скрипт каждые 30 минут, но с опозданием на 5–30 минут.
Поэтому скрипт смотрит на ближайший слот из расписания, заранее готовит пост
(новости + ИИ), ждёт до точной минуты и только тогда публикует.

Запуск вручную:  python run.py            — обычный режим по расписанию
                 python run.py news       — опубликовать новость прямо сейчас
                 python run.py summary    — опубликовать сводку прямо сейчас
"""
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

EARLY = timedelta(minutes=45)  # насколько заранее слот можно «взять в работу»
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
        at, kind = datetime.now(TZ), forced
        print(f"Ручной запуск: {kind}")
    else:
        now = datetime.now(TZ)
        slot = find_slot(now, set(done))
        if not slot:
            print(f"{now:%H:%M} по Вене — ближайших слотов нет, выхожу.")
            return
        at, kind = slot
        print(f"Слот {at:%H:%M} ({kind}), сейчас {now:%H:%M}.", flush=True)

    if kind == "news":
        ready = news.prepare(state, limit=1)  # ИИ работает заранее, до нужной минуты
        wait_until(at)
        news.publish(ready)
    else:
        wait_until(at)
        bot.main()  # курсы берём в момент публикации, чтобы цифры были свежими

    if not forced:
        done.append(slot_key(at))
        state["slots_done"] = done[-60:]
    if not news.DRY_RUN:
        news.save_state(state)


if __name__ == "__main__":
    main()
