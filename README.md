# status-pi

A wall-mounted BUSY/FREE board for a Raspberry Pi Zero 2 W and a Waveshare
3.5" RPi LCD (A) — a self-contained take on [Busy Bar](https://busy.app/) with
no buttons and no keyboard.

It answers one question for anyone walking past: **is this person in a
meeting?**

```
┌────────────────────────────────────────────┐
│ 09:41                    Tue 1 Sep   ● ●   │
│ ······································· │
│                                            │
│          ██████ ██  ██ ██████ ██  ██       │   BUSY / FREE / your own status
│                                            │
│ Sprint Review - until 10:30                │   what you are actually in
│ timer 12:34                                │   countdown, status expiry
└────────────────────────────────────────────┘
```

Everything is drawn as an LED dot matrix straight into the Linux framebuffer.
There is no browser, no X server and no desktop on the device: idle CPU is
effectively zero, and a clock tick rewrites 17 KB of the panel rather than all
300 KB.

## How it decides

**The microphone always wins.** In priority order:

1. Home Assistant says the work laptop's camera/mic is live → **BUSY**
2. A custom status you set from the web UI → that text
3. A countdown timer is running → the countdown
4. Otherwise → **FREE**, with the next meeting you accepted

Busy state comes from Home Assistant over a WebSocket, so the panel flips
within a second of a call starting. Meeting titles come from the Google
Calendar secret iCal URL. If Home Assistant goes away, the panel falls back to
"is a meeting happening right now?" and marks the mic indicator hollow rather
than claiming FREE it cannot vouch for.

## Hardware

- Raspberry Pi Zero 2 W (always on power and WiFi)
- Waveshare 3.5" RPi LCD (A) — ILI9486 over SPI, 480×320, landscape
- Raspberry Pi OS **Bookworm 64-bit Lite** (no desktop)

## Install

Flash Bookworm Lite with Raspberry Pi Imager, pre-seeding WiFi, SSH, your
hostname (`status-pi`) and locale. Then, over SSH:

```bash
sudo apt update
sudo apt install -y git

git clone https://github.com/<you>/status-pi.git
cd status-pi

sudo ./setup/install-display.sh     # panel: overlay + config.txt
sudo reboot

# after the reboot, confirm the panel came up
dmesg | grep -i fb_ili9486          # graphics fb1: fb_ili9486 frame buffer
cat /sys/class/graphics/fb1/virtual_size   # 480,320

sudo ./setup/install.sh             # venv, service user, systemd unit
```

Then open **http://status-pi.local:8080** from a phone or laptop and fill in
Settings. Nothing needs to be typed on the device itself.

### What to put in Settings

| Field | Where it comes from |
|---|---|
| Home Assistant URL | e.g. `http://homeassistant.local:8123` |
| Long-lived token | HA → your profile → Security → Long-lived access tokens |
| Camera/mic entity | the entity your existing camera-on automation triggers on |
| Secret iCal URL | Google Calendar → Settings for my calendars → Integrate calendar → **Secret address in iCal format** |
| Your email | the address that appears as `ATTENDEE` on your work invites |

To find the entity: open your automation in Home Assistant, switch to **Edit in
YAML**, and copy the `entity_id` from its state trigger. If that entity reports
something other than `on`/`off` (say `recording`), add that value to
**States that mean busy**.

The secret iCal URL is as sensitive as a password — anyone with it can read
your calendar. It is stored in `/etc/status-pi/config.yaml`, mode 0640, owned
by the service user, and is never sent back to the browser.

## The web UI

`http://status-pi.local:8080` is the whole control surface:

- **Now** — a live pixel-for-pixel preview of the panel, plus Home Assistant
  and calendar health
- **Custom status** — any text, a colour, and an optional expiry so a
  forgotten "BACK AT 3" does not lie all week
- **Timer** — presets or your own duration, with pause/resume
- **Upcoming** — the meetings it will show, each with an × to hide one the
  iCal feed insists on showing
- **Settings** — everything in `config.yaml`, applied without a restart

Up to 10 characters stay still on the panel; anything longer scrolls, the way
a real matrix does. Set `web.auth_token` if you would rather the UI were not
open on the LAN.

## Configuration

`/etc/status-pi/config.yaml`, documented in
[`setup/config.example.yaml`](setup/config.example.yaml). The web UI writes
this file, but editing it by hand is fine — `systemctl restart status-pi`
picks it up.

Two settings worth knowing about:

- `calendar.needs_action_is_accepted` (default **true**) — Google's secret
  feed often reports `PARTSTAT=NEEDS-ACTION` for meetings you definitely
  accepted. The default errs towards showing a meeting rather than silently
  hiding one. Set it false if you would rather only see confirmed meetings.
- `display.quiet_hours` — the (A) panel's backlight is hard-wired on, so quiet
  hours dim the *pixels* rather than the LED. `clock_only: true` reduces the
  panel to just the time overnight.

## Development

The renderer runs anywhere — no Pi required:

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
export PYTHONPATH=src

python -m status_pi --frames ./frames    # one PNG per display state
python -m status_pi --sim                # full app, panel rendered to PNG,
                                         # live at http://localhost:8080/preview.png
python -m pytest tests -q
```

`--probe` reports what the framebuffer looks like on the device:

```bash
python -m status_pi --probe
# /dev/fb1  480x320  16bpp  stride=960
```

### Layout of the code

| Path | What it does |
|---|---|
| `src/status_pi/state.py` | the priority rules — the heart of the device |
| `src/status_pi/render/matrix.py` | dot-matrix text, marquee scrolling |
| `src/status_pi/render/screens.py` | the 480×320 layout |
| `src/status_pi/render/fb.py` | RGB565 packing, dirty-row framebuffer writes |
| `src/status_pi/sources/ha.py` | Home Assistant WebSocket subscription |
| `src/status_pi/sources/cal.py` | iCal fetch, recurrence expansion, filtering |
| `src/status_pi/web/` | aiohttp control UI |
| `src/status_pi/app.py` | the tick loop that ties it together |

## Troubleshooting

**No `/dev/fb1` after installing the display.** Check `dmesg | grep -i
ili9486`. The usual causes are `dtoverlay=vc4-kms-v3d` still being active
(`install-display.sh` comments it out; confirm in `/boot/firmware/config.txt`)
or SPI being off (`dtparam=spi=on`). fbtft is deprecated upstream, so if a
kernel update ever breaks it, the fallback is a userspace ILI9486 driver over
`spidev` — only `render/fb.py` would change.

**The panel is upside down on the wall.** Change `rotate=90` to `rotate=270`
in the `dtoverlay=waveshare35a` line and reboot, or set `display.rotate: 180`
in the config for a software flip.

**BUSY never appears.** Check the Now panel: if Home Assistant shows
*disconnected*, the token or URL is wrong (`journalctl -u status-pi -f` will
say which). If it shows connected but the state never changes, the entity is
wrong — verify with Developer Tools → States in Home Assistant.

**A meeting shows that you declined.** Google's secret iCal feed is
inconsistent about attendee status. Hide it with the × in Upcoming, or set
`needs_action_is_accepted: false`.

**Meetings are hours out of date.** That feed can also lag by a few hours.
That is a Google-side limit, not a polling interval — the fix is to move
`sources/cal.py` to the Google Calendar API with OAuth, which nothing above it
would notice.

**Service will not start.** `systemctl status status-pi` and `journalctl -u
status-pi -n 50`. The unit runs as the `status-pi` user, which must be in the
`video` group to write `/dev/fb1`.

## What it costs to run

Measured against a 16 MHz SPI bus with the dirty-row diff in place:

| Update | Rows written | SPI time |
|---|---|---|
| Nothing changed | 0 | 0 ms |
| Clock ticks over | 18 / 320 | 8.6 ms |
| FREE → BUSY | 105 / 320 | 50 ms |
| Countdown, per second | 84 / 320 | 40 ms |
| First frame after boot | 320 / 320 | 154 ms |

The loop wakes once a second when nothing is moving and 8× a second only
while text is scrolling.
