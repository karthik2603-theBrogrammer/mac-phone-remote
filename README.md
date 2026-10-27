# Mac Phone Remote

**Your Mac, within reach. Even when you're not.**

A touch-first Safari remote for your iPhone. Move the pointer, click, scroll,
type, and switch between every extended display on your Mac. Built for watching
something from bed without getting up to reach your mouse.

**No live screen viewing, screen recording, microphone access, or screenshots.**
You look at your actual monitors; your phone is only the controller.

## What you get

- **Every active monitor:** select a display to move the pointer to its center.
  Movement stays within that monitor, including displays arranged to the left
  or above the primary one. Refresh discovers displays connected or removed.
- **Touch trackpad:** one finger moves, tap clicks, double-tap double-clicks,
  long-press or two-finger tap right-clicks, two fingers scroll.
- **Dedicated controls:** left/right click, scroll, arrows, Space, Escape,
  and Command-Control-F for apps that support macOS full screen.
- **Keyboard:** send a line of Unicode text; Return, Backspace, arrows, Tab,
  and common Command shortcuts. Text is not stored by the app.
- **Away from home:** a private HTTPS connection through Tailscale, including
  when the iPhone is on cellular. No router port forwarding or public server.
- **Strict pairing:** a single-use 8-digit terminal code, expiring after 10
  minutes. One pairing per run; the session expires after one hour and is bound
  to the Tailscale user. Restarting revokes it and generates a new code.
- **No iPhone app build:** use Safari, optionally Add to Home Screen.

## Requirements

- A Mac running macOS 13 or later, with Python 3.10+ and Accessibility access.
- An iPhone with a current Safari version and the Tailscale app.
- Tailscale installed, signed in, and connected on **both devices in the same
  tailnet**. Mac and iPhone need not be on the same Wi-Fi.
- Your Mac must stay awake, logged in, and have an active display.

This is an input remote, not remote desktop software. It cannot unlock your
Mac, wake it over the internet, or control a FileVault/login screen. Mirrored
screens share one desktop and cannot be controlled independently.

## Set up on your Mac

### 1. Install

Run these commands in **Terminal on the Mac**, not on the iPhone:

```sh
git clone https://github.com/karthik2603-theBrogrammer/mac-phone-remote.git
cd mac-phone-remote
python3 -m venv .venv
source .venv/bin/activate
python -m pip install .
```

Python is available from [python.org](https://www.python.org/downloads/macos/)
or Homebrew. The installer pulls in the macOS Quartz bindings automatically.
There is no Node.js, web build, or cloud account required by this app.

### 2. Allow mouse and keyboard control

```sh
mac-phone-remote --request-permission
```

Open **System Settings > Privacy & Security > Accessibility** and enable the
terminal application that runs the server (for example Terminal or iTerm), or
the Python executable if macOS lists it instead. Quit and reopen that terminal,
then reactivate the virtual environment:

```sh
cd mac-phone-remote
source .venv/bin/activate
```

**Screen Recording permission is not needed.** Do not grant it for this app.

### 3. Set up private HTTPS with Tailscale

Install [Tailscale on the Mac](https://tailscale.com/download/mac) and
[iPhone](https://tailscale.com/download/ios), sign in to the same tailnet,
and connect both devices.

Use the Tailscale CLI on the Mac:

```sh
tailscale serve --bg http://127.0.0.1:8765
tailscale serve status
```

Follow any prompt to enable HTTPS certificates for your tailnet. The output
shows a URL such as `https://my-mac.example-tailnet.ts.net`. Copy **your own**
URL, without a path. See the [official Serve guide](https://tailscale.com/kb/1312/serve).

If `tailscale` is not on your PATH, the Mac app provides the CLI at:

```sh
/Applications/Tailscale.app/Contents/MacOS/Tailscale serve --bg http://127.0.0.1:8765
/Applications/Tailscale.app/Contents/MacOS/Tailscale serve status
```

Serve's root HTTPS endpoint must be available; don't overwrite an existing
service without checking `tailscale serve status` first. Use a dedicated Mac
or configure an unused Serve HTTPS port and include that port in `--public-url`.
**Use Serve, not Funnel.** HTTPS requests without Serve's Tailscale user identity
header are rejected, including Funnel traffic. Do not forward port 8765 on your
router or put this service behind another public proxy. Real control accepts
only HTTPS origins using a `.ts.net` hostname; local HTTP is demo-only.

### 4. Start the remote

Replace this example origin with the one from your Serve output:

```sh
mac-phone-remote --public-url https://my-mac.example-tailnet.ts.net
```

Keep the terminal open. It prints a single-use 8-digit pairing code, valid for
10 minutes.
On the iPhone, make sure Tailscale is connected, open that HTTPS URL in Safari,
and enter the code. Codes are **not** put in URLs or saved to disk. A successful
pairing immediately consumes the code. The resulting session lasts one hour;
restart the companion and use its new code after logout or expiry.

Select your bedroom/TV monitor. Its pointer moves to the center. Click the
window you want to control, then use the trackpad or keyboard. Selecting a
monitor moves the pointer; it does not automatically focus a window or move
an app between displays.

**For watching videos:** click the video first, then use Space to pause/play,
arrow keys to seek if the app supports them, and Full screen if supported.
Space is an ordinary keyboard key, not a global media command.

Optional: Safari **Share > Add to Home Screen** gives you a launcher.
Native iPhone touch gestures cannot all be exercised with a desktop mouse.

## Daily use and stopping

To restart later:

```sh
cd mac-phone-remote
source .venv/bin/activate
mac-phone-remote --public-url https://my-mac.example-tailnet.ts.net
```

- Keep the Mac awake while using it; `caffeinate -i mac-phone-remote --public-url
  https://my-mac.example-tailnet.ts.net` prevents idle system sleep while the
  command runs. Display sleep/lock settings still apply; adjust them yourself
  if desired. The app does not weaken lock or power settings.
- The disconnect icon revokes the paired session. It does not reactivate the
  old code. Restart the server to pair again, switch phones, or recover from a
  used/expired code or session.
- **Ctrl+C** stops control immediately. Serve may remain configured, but
  cannot control anything when this server is stopped.
- To remove only this Serve root endpoint: `tailscale serve --https=443 off`.
  Check existing Serve configuration first; don't disable unrelated services.
- Turning off Tailscale on the iPhone disconnects it from the private remote.
- There is one paired session per server run. Input is serialized, but this
  is not a multi-user desktop. Multiple Safari tabs sharing the cookie can
  still control the same Mac.

The server is intentionally not an always-on login item. Run it only when
needed. Your Mac must remain awake, unlocked, and running the process for
remote access outside home to work.

## Privacy and trust

The service binds **only to `127.0.0.1`**. Tailscale Serve terminates HTTPS and
proxies to that loopback service. The browser does not talk to port 8765
directly. Access requires a valid `.ts.net` HTTPS configuration, Serve's
`Tailscale-User-Login` identity header, and pairing. A session is usable only
with the same Tailscale login that paired it. Normal iPhones signed in as users
are supported; tagged clients without user identity headers are not.
This project does not configure or enforce your Tailscale policies for you;
restrict the Mac's Serve endpoint to your devices/users.

The server validates Host and the exact browser Origin, requires JSON and a
custom request header for mutations, and uses HttpOnly, SameSite=Strict cookies
(also Secure and `__Host-` prefixed for all real control, with no Domain
attribute). Pairing is rate-limited to five attempts
per minute **globally** and succeeds at most once per process. Session tokens
are random and stored only as hashes in memory. Sessions have an absolute
one-hour lifetime that activity does not extend. Do not share codes or cookies.

Connections are capped at 16 concurrent handlers, with 10-second socket read
timeouts. Excess connections are closed without spawning a handler, and a
warning is logged; the phone reports a connection error rather than retrying.
Headers are checked against a 16 KiB
aggregate limit after parsing; bodies are capped at 8 KiB. Duplicate
security-sensitive headers, transfer encoding, malformed lengths, incomplete
bodies, and GET bodies are rejected. Controls are limited to 120 requests per
second per session; excess requests return 429 and are not executed or retried.
These are resource safeguards, **not a guarantee against denial of service**;
the HTTP service is intended only for the loopback/Serve deployment, not as a
general-purpose public web server.

Input also has action/value limits; there is no shell execution, file-transfer,
screenshot, or arbitrary-keycode API.

Anyone with a paired session can control the logged-in Mac as you, including
typing into a terminal if they focus one. Treat pairing as giving someone your
mouse and keyboard, not as granting limited media-only access. Don't pair
untrusted phones. A cookie copied to another device signed into the same
Tailscale user can still work until logout, expiry, or restart; identity binding
is not hardware/device attestation. Keep your phone locked and your Tailscale
account protected with MFA.

Local programs running on the Mac are outside this app's trust boundary:
identity headers are trusted only because Serve proxies to a loopback-only
listener. A local process can forge those headers. This does not isolate a
compromised Mac or defend against a malicious logged-in local user. Tailscale
operates its own network service and may relay
encrypted network packets; consult its privacy documentation separately.

Use a supported, patched Python/macOS version and current Tailscale releases.
Run as your normal user, never with `sudo`. Only grant Accessibility to a
terminal/Python installation you trust. No software review proves an
application risk-free; complete the Mac acceptance checks below before use.

### Updating an existing installation

Version 1.1 makes the strict profile the default; there is no insecure
real-control mode. Stop the companion, then:

```sh
git pull --ff-only
source .venv/bin/activate
python -m pip install .
mac-phone-remote --public-url https://my-mac.example-tailnet.ts.net
```

Replace the example URL with your own Serve origin. Pair again with the new
code; previous sessions do not survive a restart.

## Troubleshooting

| Problem | Check |
|---|---|
| Safari cannot open the URL | Both Tailscale clients must be connected to the same tailnet. Use the exact HTTPS Serve URL, not a LAN address. Check tailnet access rules and `tailscale serve status`. |
| 502 / Mac unreachable | Start the server on the same port Serve proxies to. Keep the terminal open and the Mac awake. |
| Pairing rejected | Codes are single-use and expire in 10 minutes. Logout/expiry does not reactivate them. Restart for a new code. After five attempts, wait 60 seconds. |
| Tailscale identity required | Use Tailscale Serve, not Funnel or another proxy. Connect the iPhone as a signed-in Tailscale user, not a tagged device. |
| Session no longer works | Sessions expire after one hour and require the same Tailscale login. Restart the companion and pair again. |
| Connection error / too many controls | Connection/input limits may have been reached. Check the Mac terminal and wait briefly; controls are never retried automatically. |
| Page loads but controls are denied | Match `--public-url` exactly to Safari's origin, including any non-default HTTPS port. Use the root `/` endpoint, not a subpath. |
| Pointer/typing does nothing | Recheck Accessibility permission for the running terminal/Python, quit and reopen it, restart the server, and ensure the Mac is unlocked. Some secure input fields/apps reject synthetic events. |
| Wrong display or window | Refresh monitors, select the intended screen, then left-click its target window. Keyboard input goes to the currently focused window. |
| Text behaves differently | Unicode typing is subject to the focused application's input handling. Use Return and Tab separately. Emoji/non-Latin input needs a Mac-side check in your target app. |
| Connection drops | Controls are not retried automatically: an uncertain click or key must never be duplicated. Pending controls are cleared; the page checks connection every five seconds. |
| iPhone disconnects in background | Reopen the remote; it rechecks the session. Background control is intentionally disabled. |

## Development and verification

Preview on **any OS** without sending real input:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install .
mac-phone-remote --demo
```

Open `http://127.0.0.1:8765` **on that computer** and enter its printed code.
The demo is prominently labeled and uses two fake monitors. To preview on
an iPhone, use `--demo --public-url YOUR_TAILSCALE_HTTPS_ORIGIN` with Serve.
On Windows, activate with `.venv\Scripts\Activate.ps1` instead of `source`.

Run the dependency-light test suite:

```sh
python -m unittest discover -s tests -v
```

Tests cover HTTP authentication, cookie attributes, origin/host and Tailscale
identity checks, single-use pairing (including concurrent attempts), one-hour
expiry, rate/connection limits, malformed and ambiguous HTTP requests, input
validation, display bounds, negative coordinates, disconnect handling, and
mocked Quartz event shapes. CI runs on macOS, Linux, and Windows, with a
JavaScript syntax check and real Quartz binding checks on macOS. The demo never
controls the host.

### Real-Mac acceptance checklist

Mocked tests and a Windows/browser preview do **not** prove macOS event delivery.
Before relying on this remote:

1. Grant Accessibility access and confirm the server refuses to run without it.
2. With two extended displays, select each and confirm pointer movement, edge
   clamping, taps, double-click, right-click, and two-finger scrolling.
3. Test a display above/left of the primary and a Retina/scaled display.
4. Click a text editor, send plain text and an emoji, then try Return/Backspace.
5. Focus your video app; test Space, arrows, and full-screen behavior.
6. Unplug/reconnect an external display and refresh; verify no stale display
   receives input.
7. Switch the iPhone from Wi-Fi to cellular with Tailscale connected and repeat.
8. Unpair and verify the old code cannot be reused. Restart the companion,
   verify a new code is required, and pair again.
9. Verify access from a different Tailscale user cannot reuse the session.
   Requests without Serve identity must return 403, even with a valid cookie.
10. Stop the server and verify the phone can no longer control the Mac.

### Structure

```text
src/mac_phone_remote/
  __main__.py       CLI, permission request, startup
  backend.py        Display geometry, validation, Quartz and demo backends
  server.py         Loopback HTTP server, pairing and sessions
  web/             Self-contained Safari interface; no external CDN
tests/             unittest HTTP, auth, geometry, and Quartz adapter tests
```

MIT licensed.
