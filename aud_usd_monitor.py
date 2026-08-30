"""
AUD/USD Conversion Timing Bot
-----------------------------
Purpose: You earn/spend AUD but invest in USD. This bot watches AUD/USD
against its 50-day and 100-day moving averages and uses a z-score (how
many standard deviations the current rate sits from its 100-day average)
to tell you when AUD is "strong" (good time to convert AUD -> USD) or
"weak" (good time to convert USD -> AUD).

A z-score instead of a fixed % threshold means the bands automatically
widen in choppy/volatile periods and narrow in calm ones, rather than
using one static number regardless of current volatility.

This bot does NOT execute any trade or transfer. It only sends you an
email alert so you can act manually (Wise, bank transfer, broker FX, etc).

Data source: Frankfurter API (https://frankfurter.app) - free, no API key,
daily ECB-derived FX rates with full history.

State: state.json persists the last signal tier and MA trend so we only
alert on a CHANGE, not every single day (same pattern as your other
GitHub Actions bots).
"""

import os
import sys
import json
import statistics
import datetime
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------
BASE_CCY = "AUD"
QUOTE_CCY = "USD"

MA_SHORT = 50   # fast MA, used only for trend context (golden/death cross)
MA_LONG = 100   # slow MA, the z-score is measured against this one

# Rolling standard deviation window for the z-score. Using the same
# window as MA_LONG keeps "average" and "spread" measured over the same
# lookback period.
STD_WINDOW = MA_LONG

# Lookback window to fetch. Needs to comfortably exceed MA_LONG in
# CALENDAR days since FX data only exists on weekdays (~5/7 of days).
HISTORY_DAYS = 300

# z-score tier thresholds (in standard deviations from the 100-day MA).
# z >= Z_STRONG          -> strong signal
# Z_MODERATE <= z < Z_STRONG -> moderate signal
# -Z_MODERATE < z < Z_MODERATE -> neutral
Z_STRONG = 2.0
Z_MODERATE = 1.0

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")

FRANKFURTER_HISTORY_URL = (
    "https://api.frankfurter.app/{start}..{end}?from={base}&to={quote}"
)


# ---------------------------------------------------------------------------
# DATA FETCH
# ---------------------------------------------------------------------------
def fetch_history():
    """Fetch daily AUD/USD closes for the lookback window. Returns a list of
    (date_str, rate) tuples sorted oldest -> newest."""
    end = datetime.date.today()
    start = end - datetime.timedelta(days=HISTORY_DAYS)
    url = FRANKFURTER_HISTORY_URL.format(
        start=start.isoformat(), end=end.isoformat(), base=BASE_CCY, quote=QUOTE_CCY
    )

    try:
        req = Request(url, headers={"User-Agent": "aud-usd-monitor/1.0"})
        with urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (URLError, HTTPError) as e:
        print(f"ERROR fetching Frankfurter history: {e}", file=sys.stderr)
        sys.exit(1)

    rates = data.get("rates", {})
    series = [(d, v[QUOTE_CCY]) for d, v in rates.items() if QUOTE_CCY in v]
    series.sort(key=lambda x: x[0])

    min_needed = max(MA_LONG, STD_WINDOW)
    if len(series) < min_needed:
        print(
            f"WARNING: only {len(series)} data points, need {min_needed} for "
            f"a full MA/std window. Signal will use whatever is available.",
            file=sys.stderr,
        )
    return series


# ---------------------------------------------------------------------------
# SIGNAL LOGIC
# ---------------------------------------------------------------------------
def sma(values, window):
    if len(values) < window:
        window = len(values)
    if window == 0:
        return None
    return sum(values[-window:]) / window


def rolling_stdev(values, window):
    if len(values) < window:
        window = len(values)
    if window < 2:
        return None
    return statistics.stdev(values[-window:])


def classify_tier(z):
    """z = number of standard deviations current price is above(+)/below(-)
    the 100-day MA."""
    if z >= Z_STRONG:
        return "STRONG_BUY_USD", "🟢🟢 AUD is STRONG"
    elif z >= Z_MODERATE:
        return "BUY_USD", "🟢 AUD is moderately strong"
    elif z <= -Z_STRONG:
        return "STRONG_SELL_USD", "🔴🔴 AUD is WEAK"
    elif z <= -Z_MODERATE:
        return "SELL_USD", "🔴 AUD is moderately weak"
    else:
        return "NEUTRAL", "⚪ AUD is near its average - no strong signal"


def compute_signal(series):
    dates = [d for d, _ in series]
    prices = [r for _, r in series]

    current_rate = prices[-1]
    current_date = dates[-1]

    ma_short = sma(prices, MA_SHORT)
    ma_long = sma(prices, MA_LONG)
    std_long = rolling_stdev(prices, STD_WINDOW)

    if std_long and std_long > 0:
        z_score = (current_rate - ma_long) / std_long
    else:
        z_score = 0.0

    trend = "GOLDEN_CROSS" if (ma_short and ma_long and ma_short > ma_long) else "DEATH_CROSS"

    tier_code, tier_label = classify_tier(z_score)

    return {
        "date": current_date,
        "rate": round(current_rate, 5),
        "ma_short": round(ma_short, 5) if ma_short else None,
        "ma_long": round(ma_long, 5) if ma_long else None,
        "std_long": round(std_long, 5) if std_long else None,
        "z_score": round(z_score, 2),
        "trend": trend,  # 50MA vs 100MA relationship
        "tier_code": tier_code,
        "tier_label": tier_label,
    }


# ---------------------------------------------------------------------------
# STATE PERSISTENCE
# ---------------------------------------------------------------------------
def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {"last_tier_code": None, "last_trend": None}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


# ---------------------------------------------------------------------------
# NOTIFICATION (email only)
# ---------------------------------------------------------------------------
def send_email(subject, message):
    import smtplib
    from email.mime.text import MIMEText

    smtp_host = os.environ.get("SMTP_HOST")
    smtp_port = int(os.environ.get("SMTP_PORT", "465"))
    smtp_user = os.environ.get("SMTP_USER")
    smtp_pass = os.environ.get("SMTP_PASS")
    email_to = os.environ.get("EMAIL_TO")
    email_from = os.environ.get("EMAIL_FROM", smtp_user)

    if not all([smtp_host, smtp_user, smtp_pass, email_to]):
        print("Email not configured (missing SMTP_HOST / SMTP_USER / "
              "SMTP_PASS / EMAIL_TO secrets) - printing message instead:\n")
        print(message)
        return

    msg = MIMEText(message)
    msg["Subject"] = subject
    msg["From"] = email_from
    msg["To"] = email_to

    try:
        with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=20) as server:
            server.login(smtp_user, smtp_pass)
            server.sendmail(email_from, [email_to], msg.as_string())
        print("Email alert sent.")
    except Exception as e:
        print(f"ERROR sending email: {e}", file=sys.stderr)


def format_message(signal, forced=False):
    header = "AUD/USD Signal Update" if not forced else "AUD/USD Status (forced check)"
    lines = [
        header,
        "",
        signal["tier_label"],
        "",
        f"Date: {signal['date']}",
        f"AUD/USD rate: {signal['rate']}",
        f"50-day MA: {signal['ma_short']}",
        f"100-day MA: {signal['ma_long']}",
        f"100-day std dev: {signal['std_long']}",
        f"z-score vs 100-day MA: {signal['z_score']:+.2f}",
        f"Trend (50 vs 100 MA): {signal['trend'].replace('_', ' ').title()}",
        "",
    ]

    if signal["tier_code"] in ("STRONG_BUY_USD", "BUY_USD"):
        lines.append("Suggestion: consider converting AUD -> USD now.")
    elif signal["tier_code"] in ("STRONG_SELL_USD", "SELL_USD"):
        lines.append("Suggestion: consider converting USD -> AUD now.")
    else:
        lines.append("Suggestion: no action - rate is close to its average.")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------
def main():
    force = "--force" in sys.argv  # manual run: always send, ignore de-dupe

    series = fetch_history()
    signal = compute_signal(series)
    state = load_state()

    tier_changed = signal["tier_code"] != state.get("last_tier_code")
    trend_changed = signal["trend"] != state.get("last_trend")

    print(json.dumps(signal, indent=2))

    if force or tier_changed or trend_changed:
        msg = format_message(signal, forced=force and not (tier_changed or trend_changed))
        subject = f"AUD/USD: {signal['tier_label']}"
        send_email(subject, msg)
    else:
        print("No change in tier or trend since last run - no alert sent.")

    state["last_tier_code"] = signal["tier_code"]
    state["last_trend"] = signal["trend"]
    state["last_checked"] = signal["date"]
    save_state(state)


if __name__ == "__main__":
    main()
