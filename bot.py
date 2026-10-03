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


def get_btc_week():
    """Цены BTC за 7 дней (почасовые точки) с CoinGecko: список (datetime, price)."""
    r = requests.get(
        f"{CG}/coins/bitcoin/market_chart",
        params={"vs_currency": "usd", "days": 7},
        timeout=20,
    )
    r.raise_for_status()
    return [(datetime.fromtimestamp(ts / 1000, timezone.utc), p) for ts, p in r.json()["prices"]]


def make_chart(points):
    """Рисует PNG-график цены BTC за неделю (тёмная тема под Telegram). Возвращает bytes."""
    import io
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    from matplotlib.ticker import FuncFormatter

    SURFACE, TEXT, MUTED, GRID, LINE = "#1a1a19", "#ffffff", "#c3c2b7", "#383835", "#3987e5"
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    change = (ys[-1] / ys[0] - 1) * 100

    fig, ax = plt.subplots(figsize=(10, 5.6), dpi=120)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)

    ax.plot(xs, ys, color=LINE, linewidth=2.2, solid_capstyle="round")
    ax.fill_between(xs, ys, min(ys), color=LINE, alpha=0.12, linewidth=0)
    ax.scatter([xs[-1]], [ys[-1]], s=60, color=LINE, edgecolor=SURFACE, linewidth=2, zorder=3)
    ax.annotate(
        f"${ys[-1]:,.0f}".replace(",", " "),
        (xs[-1], ys[-1]), xytext=(-10, 12), textcoords="offset points",
        ha="right", color=TEXT, fontsize=13, fontweight="bold",
    )

    ax.set_title("Bitcoin за 7 дней, USD", loc="left", color=TEXT, fontsize=16, fontweight="bold", pad=26)
    ax.text(0, 1.02, f"{'+' if change >= 0 else ''}{change:.2f}% за неделю",
            transform=ax.transAxes, color=MUTED, fontsize=12)

    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.grid(axis="x", visible=False)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.tick_params(colors=MUTED, labelsize=10, length=0)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"${v/1000:,.0f}k" if v >= 10000 else f"${v:,.0f}"))
    ax.xaxis.set_major_locator(mdates.DayLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m"))
    pad = (max(ys) - min(ys)) * 0.08 or 1
    ax.set_ylim(min(ys) - pad, max(ys) + pad * 2)
    ax.margins(x=0.01)

    buf = io.BytesIO()
    fig.tight_layout()
    fig.savefig(buf, format="png", facecolor=SURFACE)
    plt.close(fig)
    return buf.getvalue()


def send(text, chart=None):
    token = os.environ["BOT_TOKEN"]
    chat = os.environ["CHANNEL_ID"]
    if chart and len(text) <= 1024:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendPhoto",
            data={"chat_id": chat, "caption": text, "parse_mode": "HTML"},
            files={"photo": ("btc_week.png", chart, "image/png")},
            timeout=60,
        )
        if r.ok:
            return
        print("sendPhoto failed, sending text:", r.text[:200], file=sys.stderr)
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
    chart = None
    try:
        chart = make_chart(get_btc_week())
    except Exception as e:
        print("chart error:", e, file=sys.stderr)
    if os.environ.get("DRY_RUN") == "1":
        print(post)
        if chart:
            open("btc_week.png", "wb").write(chart)
            print("График сохранён в btc_week.png")
    else:
        send(post, chart)
        print("Отправлено ✅")


if __name__ == "__main__":
    main()
