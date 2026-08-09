# Aeroflot Subsidy Monitor

[Русская версия](README.md)

A configurable local Windows monitor for Aeroflot subsidized fares. It opens a
visible Chromium window, fills in Aeroflot's official search form, checks a
configured sequence of dates, and sends Telegram alerts. An optional loud local
alarm continues until it is acknowledged manually.

> This project is not affiliated with PJSC Aeroflot. It does not buy or reserve
> tickets, enter passenger or payment details, bypass CAPTCHA, or confirm legal
> declarations on the user's behalf. The website can change at any time, so run
> one supervised check before leaving the monitor unattended.

## Features

- any subsidy programme offered by the form;
- any route currently accepted by the website;
- one-way searches or independent checks of both round-trip directions;
- an ordered list of dates, including repeated high-priority dates;
- configurable passenger labels and counts;
- configurable cabin class;
- per-cycle or per-date full page refresh;
- conservative `AVAILABLE / UNAVAILABLE / UNKNOWN` classification;
- one Telegram alert per date and direction, with optional reminders;
- a persistent local alarm that requires manual acknowledgement;
- a persistent Chromium profile for cookies and manual CAPTCHA handling;
- logs, heartbeat messages, redacted diagnostics, and atomic state storage;
- a PID lock that prevents two monitor instances from running at once.

Programme names are not hard-coded. Enter the exact visible label from the
Aeroflot form, for example `Молодёжь и пенсионеры`. The same rule applies to
route, cabin, and passenger labels.

## Requirements

- Windows 10 or 11;
- Python 3.11 or newer;
- an internet connection;
- a Telegram bot if Telegram alerts are enabled;
- sleep mode disabled while monitoring.

## Quick start

Open PowerShell in the project directory:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1
.\scripts\configure.bat
.\.venv\Scripts\python.exe -m app.main --test-alert
.\scripts\run_once.bat
.\scripts\run_monitor.bat
```

The installer creates `.venv`, installs the dependencies and Chromium, and
creates local `.env` and `config.yaml` files from their examples. The
configuration wizard asks for the programme, route, dates, return date,
passengers, and notification settings. The Telegram token is entered without
echo and remains only in the ignored `.env` file.

## Manual configuration

Copy `config.example.yaml` to `config.yaml`, then edit the `route` section:

```yaml
route:
  subsidy_program: "Молодёжь и пенсионеры"
  origin: "Москва"
  destination: "Владивосток"

  check_dates:
    - "2026-09-03"
    - "2026-09-04"
    - "2026-09-03"

  trip_type: "round_trip"   # or one_way
  return_date: "2026-09-07" # null for one_way
  cabin: "Эконом"

  passengers:
    "Молодёжь": 1
```

The programme, cabin, city, and passenger values must match the visible Russian
labels on the site. Differences between `е` and `ё` are handled automatically.
Multiple passenger categories can be configured as separate mapping entries.

### Round trips

For a round trip, set:

```yaml
trip_type: "round_trip"
return_date: "2026-09-07"
```

The return date must not be earlier than any outbound date. After checking every
outbound date, the monitor always runs a separate fresh search with the cities
reversed, for example `Владивосток → Москва` on the return date. This check runs
even when every outbound result is `UNAVAILABLE`. Both directions are therefore
observed without selecting a fare or clicking Aeroflot's legal confirmation
labelled `Продолжить`.

### Refresh and date order

`check_dates` is an ordered sequence, not a set. Repeated dates are allowed.

```yaml
monitoring:
  refresh_mode: "cycle"
```

- `cycle`: reload and refill the form for the first date, then switch the other
  dates inside the freshly loaded results page;
- `date`: reload and refill the form before every date.

Cycle and per-date delays are random values inside the configured ranges. Avoid
aggressive intervals: they increase the risk of rate limiting without
guaranteeing fresher data.

## Telegram setup

Create `.env` from `.env.example`:

```dotenv
TELEGRAM_BOT_TOKEN=123456:your_token
TELEGRAM_CHAT_ID=your_chat_id
```

To obtain `TELEGRAM_CHAT_ID`:

1. Create a bot with `@BotFather` and send any message to your new bot.
2. Open `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser.
3. Copy the number from `message.chat.id`.
4. Test delivery:

```powershell
.\.venv\Scripts\python.exe -m app.main --test-alert
```

When an `AVAILABLE` result is confirmed, one message is sent for that date and
direction, regardless of how many matching flights appear. Temporary delivery
failures are retried. Recurring reminders are disabled by default and can be
enabled with `repeat_after_seconds` and `persistent_repeat_minutes` in
`config.yaml`. Delivery cannot be guaranteed while the computer is off or has
no power or internet access.

## Loud local alarm

When enabled, the monitor plays a repeating alternating signal and opens a
system acknowledgement dialog. A later check does not silence an active alarm.
Click `OK` or run:

```powershell
.\scripts\stop_alarm.bat
```

Test the alarm before relying on it overnight:

```powershell
.\scripts\test_alarm.bat
```

Windows controls the final volume and output device. Select the laptop speakers,
disable mute, and set an appropriate volume.

## Commands

| Command | Purpose |
|---|---|
| `.\scripts\configure.bat` | interactive configuration |
| `.\scripts\run_once.bat` | run one complete cycle |
| `.\scripts\run_monitor.bat` | monitor continuously |
| `.\scripts\stop_monitor.bat` | stop the instance recorded in the PID file |
| `.\scripts\test_alarm.bat` | test the local alarm |
| `python -m app.main --test-alert` | test Telegram delivery |
| `python -m app.main --diagnose` | save a redacted diagnostic report |

## CAPTCHA and result states

CAPTCHA is never bypassed. If it appears, the monitor enters a long pause, leaves
the browser visible, and sends a warning so that you can solve it manually.

`UNKNOWN` means the page did not provide enough independent evidence for a safe
decision. `UNAVAILABLE` includes an explicit negative result and a disabled
date card marked `Рейсов нет`. Calendar-strip prices alone never produce an
`AVAILABLE` result.

Logs are stored in `logs/monitor.log`; screenshots and redacted diagnostics are
stored in `artifacts/`. Full DOM, cookies, and local storage are not written to
diagnostic files.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m compileall -q app tests
```

## Data safety

Never commit the following:

- `.env` or the Telegram bot token;
- personal `config.yaml` values;
- `browser_profile/`, cookies, or browser state;
- `logs/`, `artifacts/`, `monitor_state.json`, or `.monitor.pid`;
- `.venv/`.

These paths are already covered by `.gitignore`, but always inspect `git status`
before publishing.

## Limitations

- selectors depend on the site's current visible Russian labels;
- Aeroflot controls programme, route, passenger, and fare availability;
- purchasing and legal confirmation always remain manual;
- the monitor does not replace official airline notifications;
- use reasonable polling intervals and comply with the website's terms.

## License

MIT. See `LICENSE`.
