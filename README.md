# status-pi

A wall-mounted BUSY/FREE board for a Raspberry Pi Zero 2 W and a Waveshare
3.5" RPi LCD (A) — a self-contained take on [Busy Bar](https://busy.app/) with
no buttons and no keyboard.

It answers one question for anyone walking past: **is this person in a
meeting?**

```
┌────────────────────────────────────────────┐
│ 09:41            Tue 1 Sep            ● ●  │   clock, date, health
│                                            │
│              ██████  ██  ██  ██            │
│              ██  ██  ██  ██  ██            │   BUSY / FREE / your own status
│              ██████  ██████  ██            │
│                                            │
│        Sprint Review - until 10:30         │   what you are actually in
│                 timer 12:34                │   countdown, status expiry
└────────────────────────────────────────────┘
```

Drawn straight into the Linux framebuffer. There is no browser, no X server
and no desktop on the device: idle CPU is effectively zero, and a clock tick
rewrites 7 KB of the panel rather than all 300 KB.

## How it looks

`display.style` picks one of two, switchable from Settings:

- **`mono`** (default) — Roboto Mono on a near-black ground: an 80px status
  word, a muted 22px header, a meeting line and a small tracked subline. This
  is the [Claude design](https://claude.ai/design/p/2e3be59a-cb75-48eb-b496-b47365319c9d)
  the panel is built to, with the header row at twice the design's size so
  the clock and date read from across a room.
- **`matrix`** — the original LED dot-matrix lattice, with scrolling marquees
  for anything too wide.

The status word is 80px for `BUSY`, `FREE` and countdowns; a longer custom
status steps down through smaller sizes so it stays on one line rather than
losing its second half to an ellipsis. A meeting title too wide for one line
wraps onto a second before anything is trimmed, so "Quarterly planning with
the platform team - until 16:00" arrives whole instead of as "Quarterly
planning with the…".

`display.pulse` gently fades the status word in and out (mono only). It is off
by default — it redraws the largest band on the panel about eight times a
second, roughly a quarter of this panel's SPI bandwidth.

Roboto Mono is bundled under the SIL Open Font License (`src/status_pi/fonts/`)
so the device never needs a network or a particular apt package to draw its own
screen.

## How it decides

**The microphone always wins.** In priority order:

1. Home Assistant says the work laptop's camera/mic is live → **BUSY**
2. A custom status you set from the web UI → that text
3. A countdown timer is running → the countdown
4. Otherwise → **FREE**, with the next meeting you accepted

Busy state comes from Home Assistant over a WebSocket, so the panel flips
within a second of a call starting. Meeting titles come from a calendar source
you choose (see below). If Home Assistant goes away, the panel falls back to
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

git clone https://github.com/allaboutduncan/status-pi.git
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
| Busy when the state is | `one of` for an on/off sensor, `anything except` for a sensor that names the live device |
| Calendar source | one of the three options below |
| Your email | the address that appears as `ATTENDEE` on your work invites (iCal only) |

To find the entity, run the built-in check — it walks the network, the token
and the entity in order, and lists likely candidates if the entity is wrong:

```bash
status-pi --check-ha
status-pi --check-ha --watch 60   # then toggle your camera
```

Or open your automation in Home Assistant, switch to **Edit in YAML**, and copy
the `entity_id` from its state trigger.

**If the entity does not report `on`/`off`**, set the match the other way
round. macOS's `sensor.<your_mac>_active_audio_input` reports the *name* of
whichever microphone is live and `Inactive` when none is, so listing every
value that means busy is a losing game — plug in a different headset and it
breaks. Instead:

| Busy when the state is | anything except |
| --- | --- |
| **States** | `Inactive` |

Any live microphone then reads as BUSY, including ones you have not bought
yet. `--check-ha` resolves your entity's current value to BUSY or FREE, shows
the distinct states it has taken in the last 24 hours, and recommends this
setup when it sees a non-binary entity.

## Where meetings come from

`calendar.provider` picks one of three, switchable from Settings without a
restart:

**`ics` — a Google Calendar secret iCal address.** The simplest option, and
the default. Google Calendar → Settings for my calendars → Integrate calendar
→ *Secret address in iCal format*.

If that section is missing on a work account, it is not you: private iCal
addresses are a **Workspace admin setting**, off by default (Admin console →
Apps → Google Workspace → Calendar → Sharing settings). If IT will turn it on,
it takes up to 24 hours to appear, and nothing else here has to change. A
calendar's *public* URL is no substitute — it only works if the calendar
itself is public.

**`ha` — a calendar entity in Home Assistant.** The way in when the answer
from IT is no. Whatever Home Assistant can see becomes a source: a Google
account it holds its own OAuth token for, CalDAV, Local Calendar, or the
[ha-icalendar](https://github.com/codyc1515/ha-icalendar) custom integration.
status-pi reads it over the URL and token it already has, so there is no
second credential to manage. `--check-ha` lists the calendar entities
available, and Settings offers them as a dropdown.

Two routes worth trying, in order of how likely they are to survive a work
policy: add the **Google Calendar** integration to Home Assistant and
authorise it against the work account; or, if that is blocked, share the work
calendar to a personal Google account and authorise *that* instead — the
shared calendar shows up as its own entity. If the share is free/busy only you
will get times without titles, which the panel still displays usefully.

**`none` — no calendar.** BUSY/FREE, the clock, custom statuses and timers.
Everything except meeting titles, the next-meeting line, and the calendar
fallback used while Home Assistant is unreachable.

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

A custom status is capped at 24 characters, which is what the panel can show
on one line at its smallest headline size. Set `web.auth_token` if you would
rather the UI were not open on the LAN.

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
python -m status_pi --check-ha           # diagnose the Home Assistant link
python -m status_pi --check-calendar     # diagnose the calendar feed
python -m pytest tests -q
```

### On the device

`setup/install.sh` puts a `status-pi` command on the path, so the diagnostics
run from anywhere:

```bash
status-pi --probe            # /dev/fb1  480x320  16bpp  stride=960
status-pi --check-ha         # does the Home Assistant link work
status-pi --check-calendar   # what the calendar returns
status-pi --test-pattern     # a ruler, to measure what the bezel hides
```

`python -m status_pi` works too, but only from `/opt/status-pi` — the package
is found through the current directory, which is why the wrapper exists.

### Layout of the code

| Path | What it does |
|---|---|
| `src/status_pi/state.py` | the priority rules — the heart of the device |
| `src/status_pi/render/mono.py` | the default typographic style |
| `src/status_pi/render/matrix.py` | dot-matrix text, marquee scrolling |
| `src/status_pi/render/screens.py` | the 480×320 dot-matrix layout |
| `src/status_pi/render/fb.py` | RGB565 packing, dirty-row framebuffer writes |
| `src/status_pi/sources/ha.py` | Home Assistant WebSocket subscription |
| `src/status_pi/sources/cal.py` | calendar providers: iCal, Home Assistant, none |
| `src/status_pi/diagnose.py` | `--check-ha`, and finding your camera entity |
| `src/status_pi/web/` | aiohttp control UI |
| `src/status_pi/app.py` | the tick loop that ties it together |

## Troubleshooting

**No `/dev/fb1` after installing the display.** Check `dmesg | grep -i
ili9486`. The usual causes are `dtoverlay=vc4-kms-v3d` still being active
(`install-display.sh` comments it out; confirm in `/boot/firmware/config.txt`)
or SPI being off (`dtparam=spi=on`). fbtft is deprecated upstream, so if a
kernel update ever breaks it, the fallback is a userspace ILI9486 driver over
`spidev` — only `render/fb.py` would change.

**Content is hidden under the plastic frame.** The framebuffer is the full
480×320, but the bezel overlaps some of the glass — unevenly, and differently
on every unit, so the clock's first digit can sit behind the frame. Measure it
rather than guessing:

```bash
sudo systemctl stop status-pi
sudo -u status-pi status-pi --test-pattern
sudo systemctl start status-pi
```

That draws nested labelled rectangles at 0, 5, 10, 15, 20 and 30 pixels, with
every number repeated on all four edges. Read the smallest number *fully*
visible on each edge and enter those under Settings → Display → Bezel margins
(or `margin_left` / `margin_top` / `margin_right` / `margin_bottom` in
`config.yaml`). The whole layout shifts inward by that much, and stays centred
on the visible area rather than the framebuffer — which matters, because an
uneven bezel makes those two different things.

**The panel is upside down on the wall.** Settings → Display → Orientation →
*Upside down (180°)*, then Save. It applies immediately, with no reboot, and
costs about 0.1 ms per frame — the dirty-row diff still works, the changed
band simply moves to the other end of the framebuffer.

To flip it at the driver instead — which also turns the boot console the
right way up — change `rotate=90` to `rotate=270` in the
`dtoverlay=waveshare35a` line of `/boot/firmware/config.txt` and reboot. Do
one or the other, not both, or they cancel out.

**Home Assistant shows unreachable / BUSY never appears.** Run the check —
it tells you which step is failing rather than making you guess:

```bash
status-pi --check-ha
```

`reachable` but `token rejected` means the long-lived token is wrong, expired
or revoked; make a new one. `entity does not exist` prints the likeliest
camera/microphone entities to use instead. If every step passes but the panel
never flips, `--check-ha --watch 60` streams the entity while you toggle your
camera — no changes there means the automation reacts to a different entity.
The Now panel shows the same underlying error under the health dots.

**The calendar entity is listed but no meetings appear.** Run:

```bash
status-pi --check-calendar
status-pi --check-calendar --raw   # the untouched response
```

It fetches through the same code path the app uses and shows what survives
each stage, which separates the three things "0 events" can mean: the request
failed, the calendar really is empty for the window we ask for (6 hours back
to 36 hours ahead), or everything was filtered out. It also flags meetings
that exist but sit beyond `lookahead_hours`, which look identical to "nothing
was fetched" from the panel. The Now panel reports the same reason under the
health dots.

**A meeting shows that you declined.** Google's secret iCal feed is
inconsistent about attendee status. Hide it with the × in Upcoming, or set
`needs_action_is_accepted: false`. (Filtering is Home Assistant's job under
the `ha` provider, so this only applies to `ics`.)

**"Secret address in iCal format" is missing from Google Calendar.** It is
disabled by default on Workspace accounts and only your admin can turn it on.
See [Where meetings come from](#where-meetings-come-from) for the two routes
that do not need it.

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
| Clock ticks over | 16 / 320 | 7.7 ms |
| FREE → BUSY | 59 / 320 | 28 ms |
| Countdown, per second | 59 / 320 | 28 ms |
| First frame after boot | 320 / 320 | 154 ms |

The loop wakes once a second when nothing is moving, and 8× a second only
while something is animating — a `matrix` marquee, or the `mono` pulse if you
turn it on. The `matrix` style costs roughly twice as much per update (105 rows for
FREE → BUSY) because the lattice lights far more pixels.
