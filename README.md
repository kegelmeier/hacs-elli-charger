# Elli Charger for Home Assistant

Read-only Home Assistant integration for Elli wallboxes (Elli Charger Connect and Pro) through the Elli cloud, the same API the Elli app uses.

This is a maintained fork of [mawiak/hacs-elli-charger](https://github.com/mawiak/hacs-elli-charger). Version 1.0.0 there logs in with email and password, which Elli's login page no longer allows (it has a Cloudflare Turnstile check). This fork logs in through your browser instead and stores only a refresh token.

## What you get

One device per wallbox on your Elli account, with these sensors:

| Sensor | Meaning |
|---|---|
| Status | `idle`, `connected` or `charging` |
| Charging power | Current charging power in W, 0 when nothing charges |
| Session energy | kWh of the running session, or of the last one when idle |
| Total energy | Lifetime kWh from all completed charging records plus the running session. Use this one in the Energy dashboard. |
| Last session | State of the running session, or details of the last completed one as attributes (RFID card, start, end, energy) |

Limits, all on Elli's side:

- **Read-only.** Elli's API has no start, stop or current-limit calls.
- **Slow.** The cloud updates every few minutes; the default poll interval is 5 minutes (2–60 in the options). For real-time power, put a meter on the wallbox circuit.
- Firmware version and model show on the device page.

## Installation

HACS → ⋮ → Custom repositories → `https://github.com/kegelmeier/hacs-elli-charger`, category Integration → download **Elli Charger** → restart Home Assistant.

## Setup

Settings → Devices & services → Add integration → **Elli Charger**. The dialog shows a login link:

1. On a computer, open the browser's developer tools (F12) on the **Console** tab, then open the login link in that same tab.
2. Log in with your Elli account. Elli then tries to open the Elli app via a `com.elli.ios.emsp://...` address, which fails in a desktop browser.
3. Copy that address from the console error ("Failed to launch 'com.elli.ios.emsp://…'") and paste it into the dialog. If your browser shows no console error, look in the **Network** tab (with "Preserve log" on) for the last redirect; its `Location` header is the address.

The link works once and only for a few minutes. After any error the dialog shows a fresh link.

If Elli ever revokes the stored login, Home Assistant shows a **Reauthenticate** notice that runs the same steps.

### Upgrading from 1.0.0

Your existing entry asks you to reauthenticate once. Entity IDs and their history are kept; the status sensor now reports lowercase states (`idle` / `connected` / `charging`).

## Privacy

The integration stores the OAuth refresh token in the config entry, never your password. It talks to `login.elli.eco` and `api.elli.eco` only.

## Development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements_test.txt
pytest
```

## License

MIT, see [LICENSE](LICENSE). Original work by Marc Szymkowiak.
