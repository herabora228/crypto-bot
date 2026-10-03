"""Авто-новости о крипте в Telegram-канал: RSS -> Gemini (бесплатно) -> пост.

За один запуск публикует NEWS_PER_RUN свежих новостей, которых ещё не было в канале.
Уже опубликованные ссылки хранятся в posted.json (GitHub Actions коммитит его обратно).

Переменные окружения:
  BOT_TOKEN       — токен от @BotFather
  CHANNEL_ID      — @username канала или -100...
  GEMINI_API_KEY  — бесплатный ключ с aistudio.google.com
  GEMINI_MODEL    — (необяз.) модель, по умолчанию gemini-3.5-flash
  NEWS_PER_RUN    — (необяз.) сколько новостей за запуск, по умолчанию 1
  MAX_AGE_HOURS   — (необяз.) брать новости не старше N часов, по умолчанию 12
  CHANNEL_STYLE   — (необяз.) описание стиля канала для ИИ
  DRY_RUN=1       — только напечатать посты, ничего не отправлять и не сохранять
"""
import calendar
import html
import json
import os
import re
import sys
import time
from pathlib import Path

import feedparser
import requests

FEEDS = [
    "https://cointelegraph.com/rss",
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://decrypt.co/feed",
    "https://forklog.com/feed",
]

STATE_FILE = Path(__file__).with_name("posted.json")
MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")
FALLBACK_MODEL = "gemini-3.5-flash-lite"
NEWS_PER_RUN = int(os.environ.get("NEWS_PER_RUN", "1"))
MAX_AGE_HOURS = float(os.environ.get("MAX_AGE_HOURS", "12"))
DRY_RUN = os.environ.get("DRY_RUN") == "1"
STYLE = os.environ.get(
    "CHANNEL_STYLE",
    "живой, понятный новичкам, без воды, немного эмодзи, без кликбейта",
)

# Слова-маркеры рекламы и мусора — такие новости пропускаем
SKIP_WORDS = ("sponsored", "press release", "advertorial", "price prediction")


# ---------- состояние ----------

def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"posted": []}


def save_state(state):
    state["posted"] = state["posted"][-500:]  # храним последние 500 ссылок
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


# ---------- новости ----------

def clean_html(text):
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def find_image(entry):
    for key in ("media_content", "media_thumbnail"):
        for m in entry.get(key, []) or []:
            if m.get("url"):
                return m["url"]
    for enc in entry.get("enclosures", []) or []:
        if enc.get("type", "").startswith("image") and enc.get("href"):
            return enc["href"]
    return None


def fetch_news():
    items = []
    now = time.time()
    for url in FEEDS:
        try:
            resp = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0 crypto-news-bot"})
            resp.raise_for_status()
            feed = feedparser.parse(resp.content)
        except Exception as e:
            print(f"feed error {url}: {e}", file=sys.stderr)
            continue
        source = feed.feed.get("title", url)
        for e in feed.entries:
            t = e.get("published_parsed") or e.get("updated_parsed")
            ts = calendar.timegm(t) if t else now
            if now - ts > MAX_AGE_HOURS * 3600:
                continue
            title = clean_html(e.get("title"))
            summary = clean_html(e.get("summary") or e.get("description"))
            if any(w in (title + " " + summary).lower() for w in SKIP_WORDS):
                continue
            items.append({
                "title": title,
                "summary": summary[:1500],
                "link": e.get("link", ""),
                "source": source,
                "ts": ts,
                "image": find_image(e),
            })
    items.sort(key=lambda x: x["ts"], reverse=True)
    return items


def norm(title):
    return re.sub(r"[^a-zа-я0-9]", "", title.lower())[:60]


# ---------- Gemini ----------

PROMPT = """Ты редактор русскоязычного Telegram-канала о криптовалютах.
Стиль канала: {style}.

Вот новость из источника «{source}»:
Заголовок: {title}
Анонс: {summary}

Напиши пост на русском СВОИМИ словами (не переводи дословно, не копируй фразы).
Используй только факты из заголовка и анонса — ничего не выдумывай: цифры, имена и даты
бери только оттуда. Если фактов мало, пост просто будет короче.

Верни строго JSON:
{{
  "relevant": true/false,   // false, если это реклама, мусор или не про крипту/блокчейн
  "emoji": "одно эмодзи по теме",
  "headline": "цепляющий заголовок до 80 символов",
  "body": "2–4 коротких предложения: что произошло и почему это важно",
  "takeaway": "одна фраза: что это значит для рынка или для обычного держателя"
}}"""


def gemini(prompt):
    key = os.environ["GEMINI_API_KEY"]
    last_err = None
    for model in dict.fromkeys([MODEL, FALLBACK_MODEL]):
        for attempt in range(3):
            r = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                headers={"x-goog-api-key": key},
                json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"responseMimeType": "application/json", "temperature": 0.7},
                },
                timeout=60,
            )
            if r.status_code == 429 or r.status_code >= 500:
                last_err = f"{model}: {r.status_code} {r.text[:200]}"
                time.sleep(10 * (attempt + 1))
                continue
            if r.status_code == 404:
                last_err = f"{model}: модель не найдена"
                break  # пробуем запасную модель
            if not r.ok:
                raise SystemExit(f"Gemini error {r.status_code}: {r.text[:500]}")
            text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
            text = re.sub(r"^```(?:json)?|```$", "", text.strip()).strip()
            return json.loads(text)
    raise SystemExit(f"Gemini недоступен: {last_err}")


def build_post(item, ai):
    e = html.escape
    return (
        f"{e(ai.get('emoji', '📰'))} <b>{e(ai['headline'])}</b>\n\n"
        f"{e(ai['body'])}\n\n"
        f"💡 {e(ai['takeaway'])}\n\n"
        f"<a href=\"{e(item['link'], quote=True)}\">Источник: {e(item['source'])}</a>"
    )


# ---------- Telegram ----------

def tg(method, payload):
    token = os.environ["BOT_TOKEN"]
    r = requests.post(f"https://api.telegram.org/bot{token}/{method}", json=payload, timeout=30)
    return r


def send(text, image=None):
    chat = os.environ["CHANNEL_ID"]
    if image and len(text) <= 1024:
        r = tg("sendPhoto", {"chat_id": chat, "photo": image, "caption": text, "parse_mode": "HTML"})
        if r.ok:
            return
        print("sendPhoto failed, sending text:", r.text[:200], file=sys.stderr)
    r = tg("sendMessage", {
        "chat_id": chat, "text": text, "parse_mode": "HTML",
        "link_preview_options": {"is_disabled": False},
    })
    if not r.ok:
        raise SystemExit(f"Telegram error {r.status_code}: {r.text}")


# ---------- main ----------

def main():
    state = load_state()
    seen = set(state["posted"])
    news = [n for n in fetch_news() if n["link"] not in seen and norm(n["title"]) not in seen]
    if not news:
        print("Свежих новостей нет.")
        return

    published, used_titles = 0, set()
    for item in news:
        if published >= NEWS_PER_RUN:
            break
        if norm(item["title"]) in used_titles:
            continue
        ai = gemini(PROMPT.format(style=STYLE, **item))
        # помечаем как просмотренную в любом случае, чтобы не гонять её повторно
        state["posted"] += [item["link"], norm(item["title"])]
        used_titles.add(norm(item["title"]))
        if not ai.get("relevant", True):
            print("Пропущено (нерелевантно):", item["title"])
            continue
        post = build_post(item, ai)
        if DRY_RUN:
            print(post, "\n" + "-" * 40)
        else:
            send(post, item.get("image"))
            print("Опубликовано:", item["title"])
        published += 1
        time.sleep(5)

    if not DRY_RUN:
        save_state(state)


if __name__ == "__main__":
    main()
