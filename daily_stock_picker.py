"""
Daily Indian Stock Picker -> Telegram
Roj 10 sector-wise stocks: technical + fundamental + news score.
Research idea only, financial advice nathi. Profit ni guarantee nathi.

Setup:
  pip install yfinance pandas requests
  export TELEGRAM_TOKEN="botfather_token"
  export TELEGRAM_CHAT_ID="your_chat_id"
  python daily_stock_picker.py
"""
import os
import datetime as dt
import io
import html
import smtplib
from email.message import EmailMessage
import urllib.parse
import xml.etree.ElementTree as ET
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
import requests
import yfinance as yf

# Sector -> stocks (NSE). Tame potano list badli shako.
UNIVERSE = {
    "Banking": ["HDFCBANK", "ICICIBANK", "SBIN"],
    "IT": ["TCS", "INFY", "HCLTECH"],
    "Pharma": ["SUNPHARMA", "CIPLA", "DRREDDY"],
    "Auto/EV": ["M&M", "MARUTI", "BAJAJ-AUTO"],
    "Power/Energy": ["NTPC", "TATAPOWER", "RELIANCE"],
    "Defence": ["HAL", "BEL", "BDL"],
    "Infra/Cement": ["LT", "ADANIPORTS", "ULTRACEMCO"],
    "FMCG": ["HINDUNILVR", "ITC", "NESTLEIND"],
    "Metals": ["TATASTEEL", "JSWSTEEL", "HINDALCO"],
    "Finance/Consumer": ["BAJFINANCE", "BHARTIARTL", "TITAN"],
}

POS = ["profit", "growth", "record", "beat", "surge", "upgrade", "order win",
       "expansion", "strong", "buy", "dividend", "bags", "wins"]
NEG = ["loss", "fraud", "probe", "downgrade", "penalty", "fall", "plunge",
       "miss", "weak", "sell", "default", "raid", "ban", "cut", "decline"]


def rsi(close, n=14):
    d = close.diff()
    up = d.clip(lower=0).rolling(n).mean()
    dn = (-d.clip(upper=0)).rolling(n).mean()
    return 100 - 100 / (1 + up / dn)


def atr(df, n=14):
    tr = pd.concat([
        df["High"] - df["Low"],
        (df["High"] - df["Close"].shift()).abs(),
        (df["Low"] - df["Close"].shift()).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(n).mean()


def technical(df):
    c = df["Close"]
    sma50, sma200 = c.rolling(50).mean(), c.rolling(200).mean()
    ema12, ema26 = c.ewm(span=12).mean(), c.ewm(span=26).mean()
    macd = ema12 - ema26
    sig = macd.ewm(span=9).mean()
    r = rsi(c).iloc[-1]
    vol_ratio = df["Volume"].tail(5).mean() / df["Volume"].tail(20).mean()
    score, why = 0, []
    if c.iloc[-1] > sma50.iloc[-1]:
        score += 1; why.append(">50DMA")
    if sma50.iloc[-1] > sma200.iloc[-1]:
        score += 1; why.append("uptrend")
    if 45 <= r <= 65:
        score += 1; why.append(f"RSI {r:.0f}")
    elif r > 75:
        score -= 1; why.append("overbought")
    if macd.iloc[-1] > sig.iloc[-1]:
        score += 1; why.append("MACD+")
    if vol_ratio > 1.2:
        score += 1; why.append("volume up")
    return score, why


def fundamental(info):
    score, why = 0, []
    de, roe, eg = info.get("debtToEquity"), info.get("returnOnEquity"), info.get("earningsGrowth")
    if de is not None and de < 100:
        score += 1; why.append("low debt")
    if roe is not None and roe > 0.15:
        score += 1; why.append(f"ROE {roe*100:.0f}%")
    if eg is not None and eg > 0.10:
        score += 1; why.append(f"profit gr {eg*100:.0f}%")
    return score, why


def news_score(sym):
    """Free Google News RSS (koi API key nathi joiti)."""
    score, heads = 0, []
    try:
        q = urllib.parse.quote(f"{sym} share NSE when:2d")
        url = f"https://news.google.com/rss/search?q={q}&hl=en-IN&gl=IN&ceid=IN:en"
        r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        for item in ET.fromstring(r.content).iter("item"):
            title = item.findtext("title") or ""
            t = title.lower()
            score += sum(w in t for w in POS) - sum(w in t for w in NEG)
            heads.append(title)
            if len(heads) >= 8:
                break
    except Exception as e:
        print("news error", sym, e)
    return max(-2, min(2, score)), heads[:2]


def make_chart(sym, df, stop, target):
    d = df.tail(180)
    c = df["Close"]
    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.plot(d.index, d["Close"], label="Price", color="#1f77b4")
    ax.plot(d.index, c.rolling(50).mean().tail(180), label="50DMA", color="orange")
    ax.plot(d.index, c.rolling(200).mean().tail(180), label="200DMA", color="red")
    ax.axhline(stop, color="crimson", ls="--", lw=1, label=f"SL {stop:.0f}")
    ax.axhline(target, color="green", ls="--", lw=1, label=f"Target {target:.0f}")
    ax.set_title(f"{sym} - 6 mahina")
    ax.legend(loc="upper left", fontsize=8)
    ax.grid(alpha=0.3)
    buf = io.BytesIO()
    fig.tight_layout()
    fig.savefig(buf, format="png", dpi=110)
    plt.close(fig)
    buf.seek(0)
    return buf


def analyse(sym):
    t = yf.Ticker(sym + ".NS")
    df = t.history(period="1y")
    if len(df) < 200:
        return None
    ts, tw = technical(df)
    fs, fw = fundamental(t.info or {})
    ns, heads = news_score(sym)
    price, a = df["Close"].iloc[-1], atr(df).iloc[-1]
    return {
        "sym": sym, "score": ts + fs + ns, "price": price,
        "stop": price - 1.5 * a, "target": price + 3 * a,
        "why": ", ".join(tw + fw), "news": heads[0] if heads else "-",
        "newsscore": ns, "df": df,
    }


def build_report():
    picks = []
    for sector, syms in UNIVERSE.items():
        res = []
        for s in syms:
            try:
                r = analyse(s)
                if r:
                    r["sector"] = sector
                    res.append(r)
            except Exception as e:
                print("skip", s, e)
        if res:
            picks.append(max(res, key=lambda x: x["score"]))
    picks.sort(key=lambda x: x["score"], reverse=True)
    picks = picks[:10]

    lines = [f"📊 Aaj na top {len(picks)} stocks ({dt.date.today():%d-%b-%Y})\n"]
    for i, p in enumerate(picks, 1):
        lines.append(
            f"{i}. {p['sym']} ({p['sector']}) | Score {p['score']}\n"
            f"   Entry ~₹{p['price']:.1f} | SL ₹{p['stop']:.1f} | Target ₹{p['target']:.1f}\n"
            f"   Kaaran: {p['why']}\n"
            f"   News ({p['newsscore']:+d}): {p['news'][:90]}\n"
        )
    lines.append("⚠️ Research idea only, SEBI advice nathi. Hamesha stop-loss rakho, "
                 "ek stock ma 10% thi vadhu nahi. Profit ni guarantee nathi.")
    return "\n".join(lines), picks


def send_telegram(text, picks=()):
    token, chat = os.environ.get("TELEGRAM_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        print(text)
        print("\n(Telegram keys set nathi, etle fakt print karyu)")
        return
    api = f"https://api.telegram.org/bot{token}"
    for i in range(0, len(text), 4000):
        requests.post(f"{api}/sendMessage",
                      data={"chat_id": chat, "text": text[i:i + 4000]}, timeout=30)
    for p in picks:
        try:
            img = make_chart(p["sym"], p["df"], p["stop"], p["target"])
            cap = f"{p['sym']} | Entry {p['price']:.0f} | SL {p['stop']:.0f} | Target {p['target']:.0f}"
            requests.post(f"{api}/sendPhoto", data={"chat_id": chat, "caption": cap},
                          files={"photo": img}, timeout=60)
        except Exception as e:
            print("chart error", p["sym"], e)


def send_email(text, picks):
    user = os.environ.get("EMAIL_USER")
    pw = os.environ.get("EMAIL_APP_PASSWORD")
    to = os.environ.get("EMAIL_TO") or user
    if not user or not pw:
        return False
    msg = EmailMessage()
    msg["Subject"] = f"Aaj na top {len(picks)} stocks - {dt.date.today():%d-%b-%Y}"
    msg["From"], msg["To"] = user, to
    msg.set_content(text)

    parts = [f"<h2>Aaj na top {len(picks)} stocks ({dt.date.today():%d-%b-%Y})</h2>"]
    charts = []
    for i, p in enumerate(picks, 1):
        cid = f"chart{i}"
        parts.append(
            f"<h3>{i}. {html.escape(p['sym'])} ({html.escape(p['sector'])}) - Score {p['score']}</h3>"
            f"<p><b>Entry</b> ~Rs {p['price']:.1f} | <b>Stop-loss</b> Rs {p['stop']:.1f} | "
            f"<b>Target</b> Rs {p['target']:.1f}<br>"
            f"<b>Kaaran:</b> {html.escape(p['why'])}<br>"
            f"<b>News ({p['newsscore']:+d}):</b> {html.escape(p['news'])}</p>"
            f'<img src="cid:{cid}" style="max-width:100%"><hr>'
        )
        try:
            charts.append((cid, make_chart(p["sym"], p["df"], p["stop"], p["target"]).read()))
        except Exception as e:
            print("chart error", p["sym"], e)
    parts.append("<p><i>Research idea only, SEBI advice nathi. Hamesha stop-loss rakho, "
                 "ek stock ma 10% thi vadhu nahi. Profit ni guarantee nathi.</i></p>")
    msg.add_alternative("".join(parts), subtype="html")
    html_part = msg.get_payload()[1]
    for cid, data in charts:
        html_part.add_related(data, "image", "png", cid=f"<{cid}>")

    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=60) as smtp:
        smtp.login(user, pw)
        smtp.send_message(msg)
    return True


if __name__ == "__main__":
    report, top = build_report()
    sent = False
    try:
        sent = send_email(report, top)
    except Exception as e:
        print("email error:", e)
        raise
    if os.environ.get("TELEGRAM_TOKEN"):
        send_telegram(report, top)
        sent = True
    if not sent:
        print(report)
        print("\n(Email/Telegram keys set nathi, etle fakt print karyu)")
