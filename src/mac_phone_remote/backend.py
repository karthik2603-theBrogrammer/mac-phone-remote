"""Input backends. Quartz is imported only on a real Mac."""

import math
import sys
import threading
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Display:
    id: str
    name: str
    x: float
    y: float
    width: float
    height: float
    primary: bool = False

    def clamp(self, x, y):
        return (
            max(self.x, min(self.x + self.width - 1, x)),
            max(self.y, min(self.y + self.height - 1, y)),
        )

    def contains(self, x, y):
        return self.x <= x < self.x + self.width and self.y <= y < self.y + self.height


KEYS = {
    "enter": 36, "tab": 48, "space": 49, "backspace": 51, "escape": 53,
    "left": 123, "right": 124, "down": 125, "up": 126,
    "home": 115, "end": 119, "pageup": 116, "pagedown": 121,
}
SHORTCUTS = {
    "copy": (8, "command"), "paste": (9, "command"),
    "cut": (7, "command"), "undo": (6, "command"),
    "select-all": (0, "command"), "find": (3, "command"),
    "spotlight": (49, "command"), "switch-app": (48, "command"),
    "close-tab": (13, "command"), "fullscreen": (3, "command-control"),
}


def number(value, name, limit):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number.")
    if abs(value) > limit or not math.isfinite(value):
        raise ValueError(f"{name} must be finite and between {-limit} and {limit}.")
    return value


class Controller:
    """Serialize input so display selection and pointer actions stay atomic."""

    demo = False

    def __init__(self):
        self.lock = threading.RLock()

    def state(self):
        with self.lock:
            return {"demo": self.demo, "displays": [asdict(d) for d in self.displays()]}

    def execute(self, data):
        with self.lock:
            action = data.get("action")
            if not isinstance(action, str) or action not in {"focus", "move", "click", "scroll", "text", "key", "shortcut"}:
                raise ValueError("Unknown input action.")
            if action == "text":
                text = data.get("text")
                if not isinstance(text, str) or not 1 <= len(text) <= 1000:
                    raise ValueError("Text must contain 1 to 1000 characters.")
                if any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in text):
                    raise ValueError("Send printable text; use the Enter or Tab buttons separately.")
                self.type_text(text)
                return
            if action in {"key", "shortcut"}:
                key = data.get("key")
                allowed = KEYS if action == "key" else SHORTCUTS
                if not isinstance(key, str) or key not in allowed:
                    raise ValueError("Unsupported key or shortcut.")
                self.press_key(key, shortcut=action == "shortcut")
                return
            display = next((d for d in self.displays() if d.id == data.get("display")), None)
            if display is None:
                raise ValueError("Display disconnected. Refresh the monitor list.")
            if action == "focus":
                self.move_to(display.x + display.width / 2, display.y + display.height / 2)
            elif action == "move":
                dx = number(data.get("dx"), "dx", 2000)
                dy = number(data.get("dy"), "dy", 2000)
                x, y = self.position()
                if not display.contains(x, y):
                    x, y = display.x + display.width / 2, display.y + display.height / 2
                self.move_to(*display.clamp(x + dx, y + dy))
            else:
                if action == "click":
                    button = data.get("button", "left")
                    count = data.get("count", 1)
                    if button not in ("left", "right") or type(count) is not int or count not in (1, 2):
                        raise ValueError("Click must use left/right and a count of 1 or 2.")
                else:
                    dx = number(data.get("dx"), "dx", 1000)
                    dy = number(data.get("dy"), "dy", 1000)
                x, y = self.position()
                if not display.contains(x, y):
                    self.move_to(display.x + display.width / 2, display.y + display.height / 2)
                if action == "click":
                    self.click(button, count)
                else:
                    self.scroll(round(dx), round(dy))


class MacController(Controller):
    def __init__(self):
        super().__init__()
        if sys.platform != "darwin":
            raise RuntimeError("Mac control requires macOS. Use --demo to preview without controlling a computer.")
        import Quartz
        self.q = Quartz
        self.check_permission()

    def check_permission(self):
        if not self.q.CGPreflightPostEventAccess():
            raise RuntimeError(
                "Accessibility permission is required. Run with --request-permission, then enable "
                "your terminal (or Python) in System Settings > Privacy & Security > Accessibility. "
                "Quit and reopen the terminal before retrying."
            )

    def execute(self, data):
        self.check_permission()
        super().execute(data)

    def state(self):
        self.check_permission()
        return super().state()

    def displays(self):
        q = self.q
        error, ids, count = q.CGGetActiveDisplayList(32, None, None)
        if error:
            raise RuntimeError(f"Unable to enumerate displays (Quartz error {error}).")
        result = []
        for index, display_id in enumerate(ids[:count]):
            # Mirrored displays are one desktop target, not independently addressable screens.
            if q.CGDisplayMirrorsDisplay(display_id) != q.kCGNullDirectDisplay:
                continue
            bounds = q.CGDisplayBounds(display_id)
            primary = display_id == q.CGMainDisplayID()
            name = "Built-in display" if q.CGDisplayIsBuiltin(display_id) else f"External display {index + 1}"
            result.append(Display(
                str(display_id), name, bounds.origin.x, bounds.origin.y,
                bounds.size.width, bounds.size.height, primary,
            ))
        if not result:
            raise RuntimeError("No active displays. Wake your Mac and reconnect a display.")
        return result

    def position(self):
        point = self.q.CGEventGetLocation(self.q.CGEventCreate(None))
        return point.x, point.y

    def move_to(self, x, y):
        q = self.q
        event = q.CGEventCreateMouseEvent(None, q.kCGEventMouseMoved, (x, y), q.kCGMouseButtonLeft)
        q.CGEventPost(q.kCGHIDEventTap, event)

    def click(self, button, count):
        q = self.q
        right = button == "right"
        down = q.kCGEventRightMouseDown if right else q.kCGEventLeftMouseDown
        up = q.kCGEventRightMouseUp if right else q.kCGEventLeftMouseUp
        mouse_button = q.kCGMouseButtonRight if right else q.kCGMouseButtonLeft
        position = self.position()
        for click_number in range(1, count + 1):
            for kind in (down, up):
                event = q.CGEventCreateMouseEvent(None, kind, position, mouse_button)
                q.CGEventSetIntegerValueField(event, q.kCGMouseEventClickState, click_number)
                q.CGEventPost(q.kCGHIDEventTap, event)

    def scroll(self, dx, dy):
        q = self.q
        event = q.CGEventCreateScrollWheelEvent(None, q.kCGScrollEventUnitPixel, 2, dy, dx)
        q.CGEventPost(q.kCGHIDEventTap, event)

    def type_text(self, text):
        q = self.q
        for start in range(0, len(text), 20):
            chunk = text[start:start + 20]
            for down in (True, False):
                event = q.CGEventCreateKeyboardEvent(None, 0, down)
                q.CGEventKeyboardSetUnicodeString(event, len(chunk.encode("utf-16-le")) // 2, chunk)
                q.CGEventPost(q.kCGHIDEventTap, event)

    def press_key(self, key, shortcut=False):
        q = self.q
        code = SHORTCUTS[key][0] if shortcut else KEYS[key]
        flags = q.kCGEventFlagMaskCommand if shortcut else 0
        if shortcut and SHORTCUTS[key][1] == "command-control":
            flags |= q.kCGEventFlagMaskControl
        for down in (True, False):
            event = q.CGEventCreateKeyboardEvent(None, code, down)
            q.CGEventSetFlags(event, flags)
            q.CGEventPost(q.kCGHIDEventTap, event)


class DemoController(Controller):
    """Explicit preview mode: records input in memory, never touches the host OS."""

    demo = True

    def __init__(self):
        super().__init__()
        self.cursor = (720, 450)
        self.last_action = None

    def displays(self):
        return [
            Display("1", "MacBook display", 0, 0, 1440, 900, True),
            Display("2", "Bedroom display", 1440, -180, 1920, 1080),
        ]

    def position(self):
        return self.cursor

    def move_to(self, x, y):
        self.cursor = (x, y)

    def click(self, button, count):
        self.last_action = ("click", button, count)

    def scroll(self, dx, dy):
        self.last_action = ("scroll", dx, dy)

    def type_text(self, text):
        self.last_action = ("text", text)

    def press_key(self, key, shortcut=False):
        self.last_action = ("shortcut" if shortcut else "key", key)
