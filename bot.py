"""Ежедневная крипто-сводка в Telegram-канал.

Переменные окружения:
  BOT_TOKEN   — токен от @BotFather
  CHANNEL_ID  — @username канала или числовой id (-100...)
  COINS       — (необязательно) id монет CoinGecko через запятую
  DRY_RUN=1   — (необязательно) только напечатать пост, не отправлять
"""
import os
import sys
from datetime import datetime, timezone

import requests

CG = "https://api.coingecko.com/api/v3"
DEFAULT_COINS = "bitcoin,ethereum,solana,binancecoin,ripple,the-open-network"
COINS = [c.strip() for c in os.environ.get("COINS", DEFAULT_COINS).split(",") if c.strip()]

FNG_RU = {
    "Extreme Fear": "Крайний страх 😱",
    "Fear": "Страх 😟",
    "Neutral": "Нейтрально 😐",
    "Greed": "Жадность 🤑",
    "Extreme Greed": "Крайняя жадность 🚀",
}


def get_markets():
    r = requests.get(
        f"{CG}/coins/markets",
        params={
            "vs_currency": "usd",
            "ids": ",".join(COINS),
            "price_change_percentage": "24h,7d",
        },
        timeout=20,
    )
    r.raise_for_status()
    order = {c: i for i, c in enumerate(COINS)}
    return sorted(r.json(), key=lambda x: order.get(x["id"], 999))


def get_global():
    try:
        r = requests.get(f"{CG}/global", timeout=20)
        r.raise_for_status()
        return r.json()["data"]
    except Exception as e:
        print("global error:", e, file=sys.stderr)
        return None


def get_fng():
    try:
        r = requests.get("https://api.alternative.me/fng/?limit=1", timeout=20)
        r.raise_for_status()
        return r.json()["data"][0]
    except Exception as e:
        print("fng error:", e, file=sys.stderr)
        return None


def fmt_price(p):
    if p is None:
        return "—"
    s = f"${p:,.2f}" if p >= 1 else f"${p:.4f}"
    return s.replace(",", " ")


def fmt_pct(p):
    if p is None:
        return "—"
    return f"{'+' if p >= 0 else ''}{p:.2f}%"


def fmt_big(n):
    for div, suf in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
        if n >= div:
            return f"${n / div:.2f}{suf}"
    return f"${n:,.0f}"


def build_post(markets, glob, fng, now=None):
    now = now or datetime.now(timezone.utc)
    lines = [f"📊 <b>Крипто-сводка на {now:%d.%m.%Y}</b>", ""]

    for c in markets:
        ch24 = c.get("price_change_percentage_24h_in_currency", c.get("price_change_percentage_24h"))
        ch7 = c.get("price_change_percentage_7d_in_currency")
        dot = "🟢" if (ch24 or 0) >= 0 else "🔴"
        lines.append(
            f"{dot} <b>{c['symbol'].upper()}</b> {fmt_price(c.get('current_price'))}"
            f"  24ч: {fmt_pct(ch24)} · 7д: {fmt_pct(ch7)}"
        )

    if markets:
        best = max(markets, key=lambda x: x.get("price_change_percentage_24h") or -1e9)
        worst = min(markets, key=lambda x: x.get("price_change_percentage_24h") or 1e9)
        lines += [
            "",
            f"🏆 Лидер дня: <b>{best['symbol'].upper()}</b> ({fmt_pct(best.get('price_change_percentage_24h'))})",
            f"📉 Аутсайдер: <b>{worst['symbol'].upper()}</b> ({fmt_pct(worst.get('price_change_percentage_24h'))})",
        ]

    if glob:
        lines += [
            "",
            f"🌍 Капитализация рынка: {fmt_big(glob['total_market_cap']['usd'])}"
            f" ({fmt_pct(glob.get('market_cap_change_percentage_24h_usd'))})",
            f"₿ Доминация BTC: {glob['market_cap_percentage']['btc']:.1f}%",
        ]

    if fng:
        label = FNG_RU.get(fng["value_classification"], fng["value_classification"])
        lines.append(f"🧠 Индекс страха и жадности: <b>{fng['value']}</b> — {label}")

    lines += ["", "<i>Не является финансовым советом.</i>"]
    return "\n".join(lines)


def send(text):
    token = os.environ["BOT_TOKEN"]
    chat = os.environ["CHANNEL_ID"]
    r = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={
            "chat_id": chat,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=20,
    )
    if not r.ok:
        raise SystemExit(f"Telegram error {r.status_code}: {r.text}")


def main():
    post = build_post(get_markets(), get_global(), get_fng())
    if os.environ.get("DRY_RUN") == "1":
        print(post)
    else:
        send(post)
        print("Отправлено ✅")


if __name__ == "__main__":
    main()
