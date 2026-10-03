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
    "сухо и по делу, только факты и цифры, без воды и без кликбейта",
)

# Слова-маркеры рекламы, мусора и обзорных дайджестов — такие новости пропускаем
SKIP_WORDS = (
    "sponsored", "press release", "advertorial", "price prediction",
    "what happened in crypto today", "here's what happened", "weekly recap",
    "week in review", "roundup", "newsletter", "podcast", "дайджест", "итоги недели",
)


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


UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}


def fetch_article(url, limit=4000):
    """Достаёт текст статьи (абзацы <p>) и обложку (og:image). Возвращает (text, image_url)."""
    try:
        r = requests.get(url, timeout=20, headers=UA)
        r.raise_for_status()
        page = r.text
        paras = re.findall(r"<p[^>]*>(.*?)</p>", page, flags=re.S | re.I)
        text = re.sub(r"\s+", " ", " ".join(clean_html(p) for p in paras)).strip()
        img = None
        for pat in (
            r'<meta[^>]+property=["\']og:image(?::secure_url)?["\'][^>]+content=["\']([^"\']+)',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image',
            r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)',
        ):
            m = re.search(pat, page, flags=re.I)
            if m:
                img = html.unescape(m.group(1))
                break
        return (text[:limit] if len(text) > 200 else ""), img
    except Exception as e:
        print(f"article error {url}: {e}", file=sys.stderr)
        return "", None


def norm(title):
    return re.sub(r"[^a-zа-я0-9]", "", title.lower())[:60]


# ---------- Gemini ----------

PROMPT = """Ты редактор русскоязычного Telegram-канала о криптовалютах.
Стиль канала: {style}.

Материал:
Заголовок: {title}
Анонс: {summary}
Текст статьи (может быть пустым): {article}

Напиши пост на русском СВОИМИ словами (не переводи дословно, не копируй фразы).

ПРАВИЛА:
1. Пиши ТОЛЬКО что конкретно произошло: кто/что, какое событие, цифры, суммы, проценты,
   даты, названия монет, компаний, бирж, стран. Каждое предложение — конкретный факт.
2. Никакой воды и общих фраз. Запрещены формулировки вроде «мы собрали главные события»,
   «в центре внимания», «эксперты обсуждают», «рынок следит», «важная новость для индустрии»,
   «издание сообщает», «по данным источника».
3. НЕ упоминай СМИ, сайт или источник, откуда взята новость.
4. Исключение: если в материале есть прямое высказывание конкретного человека
   (глава компании, регулятор, аналитик, политик), его можно процитировать:
   «…», — заявил Имя Фамилия, должность.
5. Используй только факты из материала, ничего не выдумывай и не добавляй от себя.
6. Если в материале нет конкретного события и фактов (это обзор, дайджест, подборка,
   мнение без новостей, реклама, не про крипту/блокчейн) — верни "relevant": false.

Верни строго JSON:
{{
  "relevant": true/false,
  "emoji": "одно эмодзи по теме",
  "headline": "заголовок до 80 символов: суть события с главной цифрой или названием",
  "body": "2–4 коротких предложения с конкретными фактами: что произошло, цифры, детали (до 700 символов)",
  "takeaway": "одна фраза о практическом последствии события (только если оно прямо следует из фактов), иначе пустая строка"
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
                    "generationConfig": {"responseMimeType": "application/json", "temperature": 0.4},
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
    post = f"{e(ai.get('emoji', '📰'))} <b>{e(ai['headline'])}</b>\n\n{e(ai['body'])}"
    if (ai.get("takeaway") or "").strip():
        post += f"\n\n💡 {e(ai['takeaway'].strip())}"
    return post


# ---------- Telegram ----------

def tg(method, payload):
    token = os.environ["BOT_TOKEN"]
    r = requests.post(f"https://api.telegram.org/bot{token}/{method}", json=payload, timeout=30)
    return r


def send_photo(chat, image, caption):
    """Сначала даём Telegram ссылку; если не принял — скачиваем картинку и грузим файлом."""
    r = tg("sendPhoto", {"chat_id": chat, "photo": image, "caption": caption, "parse_mode": "HTML"})
    if r.ok:
        return True
    print("sendPhoto by URL failed:", r.text[:200], file=sys.stderr)
    try:
        img = requests.get(image, timeout=30, headers=UA)
        img.raise_for_status()
        if len(img.content) > 9_500_000:
            return False
        token = os.environ["BOT_TOKEN"]
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendPhoto",
            data={"chat_id": chat, "caption": caption, "parse_mode": "HTML"},
            files={"photo": ("cover.jpg", img.content)},
            timeout=60,
        )
        if r.ok:
            return True
        print("sendPhoto upload failed:", r.text[:200], file=sys.stderr)
    except Exception as e:
        print("image download failed:", e, file=sys.stderr)
    return False


def send(text, image=None):
    chat = os.environ["CHANNEL_ID"]
    if image:
        if len(text) <= 1024:
            if send_photo(chat, image, text):
                return
        else:
            # подпись к фото максимум 1024 символа: заголовок под фото, текст следом
            headline = text.split("\n\n", 1)[0]
            rest = text.split("\n\n", 1)[1] if "\n\n" in text else ""
            if send_photo(chat, image, headline):
                text = rest or text
    r = tg("sendMessage", {
        "chat_id": chat, "text": text, "parse_mode": "HTML",
        "link_preview_options": {"is_disabled": True},
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
        article, og_image = fetch_article(item["link"]) if item["link"] else ("", None)
        item["image"] = og_image or item.get("image")  # обложка статьи обычно крупнее превью из RSS
        ai = gemini(PROMPT.format(style=STYLE, article=article, **item))
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
            print("Опубликовано:", item["title"], "| картинка:", item.get("image") or "нет")
        published += 1
        time.sleep(5)

    if not DRY_RUN:
        save_state(state)


if __name__ == "__main__":
    main()
