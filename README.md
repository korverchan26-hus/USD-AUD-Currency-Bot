# AUD/USD Conversion Signal Bot

A small GitHub Actions bot that watches AUD/USD and emails you when it's a
statistically good time to convert AUD → USD (or back), based on how far
the rate has drifted from its own moving average — not a fixed target.

It does **not** move any money. It only sends an email; you convert
manually via your bank, Wise, or broker.

## Why

If you earn and spend in AUD but invest in USD, the AUD/USD rate at the
moment you convert matters. This bot removes the need to check the rate
yourself every day — it watches it for you and only speaks up when
something's actually notable.

## How the signal works

- **50-day MA** and **100-day MA** of the daily AUD/USD rate (ECB
  reference rates via the free [Frankfurter API](https://frankfurter.app),
  no key required).
- **Trend**: whether the 50-day MA is above the 100-day MA (`GOLDEN_CROSS`,
  short-term stronger than long-term) or below (`DEATH_CROSS`).
- **z-score**: `(current rate − 100-day MA) ÷ (100-day rolling std dev)`.
  This measures how many standard deviations the current rate is from its
  own recent average, so the "is this unusual?" bar automatically adjusts
  to how volatile AUD/USD currently is, instead of using a flat percentage
  that would be too tight in calm periods and too loose in choppy ones.

| z-score          | Tier             | Meaning                          |
|-------------------|------------------|-----------------------------------|
| ≥ +2               | Strong Buy USD   | AUD unusually strong               |
| +1 to +2           | Buy USD          | AUD moderately strong              |
| −1 to +1           | Neutral          | Nothing notable                    |
| −2 to −1           | Sell USD         | AUD moderately weak                |
| ≤ −2               | Strong Sell USD  | AUD unusually weak                 |

An email is only sent when the **tier** or the **trend** changes from the
previous run — not every single day — so you won't get spammed while the
rate sits still.

## Files

| File | Purpose |
|---|---|
| `aud_usd_monitor.py` | Main script: fetch data, compute signal, send email, persist state |
| `state.json` | Stores the last tier/trend so alerts only fire on change |
| `requirements.txt` | No external dependencies (stdlib only) |
| `.github/workflows/aud-usd-monitor.yml` | Runs the script daily via GitHub Actions |

## Setup

1. **Add the files** to this repo (already done if you're reading this
   here) with `.github/workflows/aud-usd-monitor.yml` at that exact path.

2. **Get SMTP credentials.** For Gmail:
   - Turn on 2-Step Verification.
   - Generate an [app password](https://myaccount.google.com/apppasswords)
     for "Mail" — this is what the bot logs in with, not your normal
     password.
   - Any other SMTP provider (Outlook, Fastmail, etc.) works too; you'll
     just need their host/port instead.

3. **Add repo secrets** — Settings → Secrets and variables → Actions →
   New repository secret:

   | Secret | Example |
   |---|---|
   | `SMTP_HOST` | `smtp.gmail.com` |
   | `SMTP_PORT` | `465` |
   | `SMTP_USER` | `you@gmail.com` |
   | `SMTP_PASS` | *(the app password)* |
   | `EMAIL_TO` | `you@gmail.com` |
   | `EMAIL_FROM` *(optional)* | defaults to `SMTP_USER` if omitted |

4. **Test it.** Go to the *Actions* tab → "AUD/USD Conversion Signal" →
   *Run workflow* → set `force` to `true` → run. This forces an email even
   with no tier change, so you can confirm the whole pipeline works.

5. **Let it run.** From then on it runs automatically once a day
   (21:00 UTC) and only emails on a real change. `state.json` is
   auto-committed back to the repo after each run.

## Tuning

- `MA_SHORT` / `MA_LONG` (default 50 / 100 days) in `aud_usd_monitor.py`
  control the trend calculation and the average the z-score is measured
  against.
- `Z_STRONG` / `Z_MODERATE` (default 2.0 / 1.0) control how many standard
  deviations count as "moderate" vs "strong."
- After a few weeks of real alerts, adjust these based on how often each
  tier actually fires rather than guessing up front.

## Disclaimer

This is a personal automation tool, not financial advice. FX rates are
volatile and can move against a signal at any time.
