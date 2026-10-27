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
- **Pairing:** an 8-digit terminal code, expiring after 10 minutes; HTTP-only
  session cookies expire after 12 hours. Restarting revokes all devices.
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
**Use Serve, not Funnel**: Funnel would expose the pairing page to the public
internet. Do not forward port 8765 on your router.

### 4. Start the remote

Replace this example origin with the one from your Serve output:

```sh
mac-phone-remote --public-url https://my-mac.example-tailnet.ts.net
```

Keep the terminal open. It prints an 8-digit pairing code, valid for 10 minutes.
On the iPhone, make sure Tailscale is connected, open that HTTPS URL in Safari,
and enter the code. Codes are **not** put in URLs or saved to disk.

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
- The disconnect icon unpairs this phone. Restart the server to revoke all
  sessions, generate a fresh code, or pair after a code has expired.
- **Ctrl+C** stops control immediately. Serve may remain configured, but
  cannot control anything when this server is stopped.
- To remove only this Serve root endpoint: `tailscale serve --https=443 off`.
  Check existing Serve configuration first; don't disable unrelated services.
- Turning off Tailscale on the iPhone disconnects it from the private remote.
- Avoid having multiple people control the Mac simultaneously. Input is
  serialized, but this is not a multi-user desktop or a permission boundary
  between paired devices.

The server is intentionally not an always-on login item. Run it only when
needed. Your Mac must remain awake, unlocked, and running the process for
remote access outside home to work.

## Privacy and trust

The service binds **only to `127.0.0.1`**. Tailscale Serve terminates HTTPS and
proxies to that loopback service. The browser does not talk to port 8765
directly. Access is limited by your tailnet's access rules, then by pairing.
This project does not configure or enforce your Tailscale policies for you;
restrict the Mac's Serve endpoint to your devices/users.

The server validates Host and the exact browser Origin, requires JSON and a
custom request header for mutations, and uses HttpOnly, SameSite=Strict cookies
(also Secure when configured with HTTPS). Pairing is rate-limited to five
attempts per minute **globally**, with at most eight active sessions.
The first successful pairing does not invalidate the code: it remains usable
for additional devices until its 10-minute expiry. Do not share the code.
Input requests have size and value limits; there is no shell execution,
file-transfer, screenshot, or arbitrary-keycode API.

Anyone with a paired session can control the logged-in Mac as you, including
typing into a terminal if they focus one. Treat pairing as giving someone your
mouse and keyboard, not as granting limited media-only access. Don't pair
untrusted phones. Local programs running on the Mac are outside this app's
trust boundary. Tailscale operates its own network service and may relay
encrypted network packets; consult its privacy documentation separately.

## Troubleshooting

| Problem | Check |
|---|---|
| Safari cannot open the URL | Both Tailscale clients must be connected to the same tailnet. Use the exact HTTPS Serve URL, not a LAN address. Check tailnet access rules and `tailscale serve status`. |
| 502 / Mac unreachable | Start the server on the same port Serve proxies to. Keep the terminal open and the Mac awake. |
| Pairing rejected | Use the latest terminal code. Codes expire in 10 minutes. After five attempts, wait 60 seconds. Restart for a new code. |
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

Tests cover HTTP authentication, cookie attributes, origin/host checks, input
validation, expiry and rate limits, display bounds, negative coordinates,
disconnect handling, and mocked Quartz event shapes. CI runs on macOS, Linux,
and Windows, with a JavaScript syntax check. The demo never controls the host.

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
8. Unpair and verify controls require a code again. Stop the server and verify
   the phone can no longer control the Mac.

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
