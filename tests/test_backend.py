import math
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from mac_phone_remote.backend import Controller, DemoController, Display, MacController


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.controller = DemoController()

    def run_action(self, action, **data):
        self.controller.execute({"action": action, "display": "2", **data})

    def test_select_external_monitor_centers_in_global_coordinates(self):
        self.run_action("focus")
        self.assertEqual(self.controller.position(), (2400, 360))

    def test_movement_clamps_to_selected_monitor(self):
        self.run_action("move", dx=2000, dy=-2000)
        self.assertEqual(self.controller.position(), (3359, -180))
        self.run_action("move", dx=-2000, dy=2000)
        self.assertEqual(self.controller.position(), (1440, 899))

    def test_monitor_above_and_left_of_primary(self):
        display = Display("3", "Left", -1920, -1080, 1920, 1080)
        self.assertEqual(display.clamp(-9999, 9999), (-1920, -1))
        self.assertTrue(display.contains(-1, -1))
        self.assertFalse(display.contains(0, 0))

    def test_click_on_another_monitor_moves_first(self):
        self.run_action("click", button="right", count=2)
        self.assertEqual(self.controller.position(), (2400, 360))
        self.assertEqual(self.controller.last_action, ("click", "right", 2))

    def test_scroll_on_another_monitor_moves_first(self):
        self.run_action("scroll", dx=12.4, dy=-22.9)
        self.assertEqual(self.controller.position(), (2400, 360))
        self.assertEqual(self.controller.last_action, ("scroll", 12, -23))

    def test_all_monitors_are_in_state_without_pixels(self):
        state = self.controller.state()
        self.assertTrue(state["demo"])
        self.assertEqual(len(state["displays"]), 2)
        self.assertEqual(set(state), {"demo", "displays"})

    def test_text_unicode_and_keys(self):
        self.run_action("text", text="Hello \U0001f600")
        self.assertEqual(self.controller.last_action, ("text", "Hello \U0001f600"))
        self.run_action("key", key="enter")
        self.assertEqual(self.controller.last_action, ("key", "enter"))
        self.run_action("shortcut", key="fullscreen")
        self.assertEqual(self.controller.last_action, ("shortcut", "fullscreen"))

    def test_disconnected_monitor_rejected(self):
        with self.assertRaisesRegex(ValueError, "disconnected"):
            self.controller.execute({"action": "click", "display": "missing"})

    def test_non_numeric_and_excessive_movement_rejected(self):
        for value in (True, None, "1", math.nan, math.inf, -math.inf, 2001, 10 ** 400):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.run_action("move", dx=value, dy=0)

    def test_invalid_scroll_does_not_move_pointer(self):
        with self.assertRaises(ValueError):
            self.run_action("scroll", dx=0, dy=1001)
        self.assertEqual(self.controller.position(), (720, 450))

    def test_invalid_click_does_not_move_pointer(self):
        for data in ({"button": "middle"}, {"count": True}, {"count": 3}, {"count": "1"}):
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.run_action("click", **data)
        self.assertEqual(self.controller.position(), (720, 450))

    def test_invalid_text_rejected(self):
        for text in ("", "a" * 1001, "\n", "a\tb", "\0", "\ud800", None, 123):
            with self.subTest(text=repr(text)), self.assertRaises(ValueError):
                self.run_action("text", text=text)

    def test_unknown_actions_and_keys_rejected(self):
        for action, data in (
            ("shell", {"command": "anything"}), ("key", {"key": "unknown"}),
            ("shortcut", {"key": "unknown"}), ("key", {"key": []}), ([], {}),
        ):
            with self.subTest(action=action, data=data), self.assertRaises(ValueError):
                self.run_action(action, **data)


class QuartzAdapterTests(unittest.TestCase):
    def setUp(self):
        # Exercise Quartz call shapes without importing a macOS framework on CI hosts.
        self.q = Mock()
        constants = [
            "kCGEventMouseMoved", "kCGMouseButtonLeft", "kCGHIDEventTap",
            "kCGEventRightMouseDown", "kCGEventRightMouseUp", "kCGMouseButtonRight",
            "kCGEventLeftMouseDown", "kCGEventLeftMouseUp", "kCGMouseEventClickState",
            "kCGScrollEventUnitPixel",
        ]
        for index, name in enumerate(constants, 1):
            setattr(self.q, name, index)
        self.q.kCGEventFlagMaskCommand = 1 << 20
        self.q.kCGEventFlagMaskControl = 1 << 18
        self.q.kCGNullDirectDisplay = 0
        self.q.CGEventGetLocation.return_value = SimpleNamespace(x=100, y=-50)
        self.controller = MacController.__new__(MacController)
        Controller.__init__(self.controller)
        self.controller.q = self.q

    def test_display_enumeration_filters_mirrors_and_preserves_geometry(self):
        self.q.CGGetActiveDisplayList.return_value = (0, (10, 20, 30), 3)
        self.q.CGDisplayMirrorsDisplay.side_effect = lambda value: 10 if value == 30 else 0
        self.q.CGMainDisplayID.return_value = 10
        self.q.CGDisplayIsBuiltin.side_effect = lambda value: value == 10
        self.q.CGDisplayBounds.return_value = SimpleNamespace(
            origin=SimpleNamespace(x=-1920, y=-200),
            size=SimpleNamespace(width=1920, height=1080),
        )
        displays = self.controller.displays()
        self.assertEqual([d.id for d in displays], ["10", "20"])
        self.assertTrue(displays[0].primary)
        self.assertEqual(displays[1].x, -1920)

    def test_display_failure_is_explicit(self):
        self.q.CGGetActiveDisplayList.return_value = (1, (), 0)
        with self.assertRaisesRegex(RuntimeError, "Quartz error"):
            self.controller.displays()

    def test_no_display_is_explicit(self):
        self.q.CGGetActiveDisplayList.return_value = (0, (), 0)
        with self.assertRaisesRegex(RuntimeError, "No active"):
            self.controller.displays()

    def test_mouse_move_posts_hid_event(self):
        self.controller.move_to(-100, 25)
        self.q.CGEventCreateMouseEvent.assert_called_once_with(
            None, self.q.kCGEventMouseMoved, (-100, 25), self.q.kCGMouseButtonLeft,
        )
        self.assertEqual(self.q.CGEventPost.call_count, 1)

    def test_double_click_posts_two_paired_events_with_click_state(self):
        self.controller.click("left", 2)
        states = [call.args[2] for call in self.q.CGEventSetIntegerValueField.call_args_list]
        self.assertEqual(states, [1, 1, 2, 2])
        self.assertEqual(self.q.CGEventPost.call_count, 4)

    def test_scroll_uses_pixel_units_and_vertical_then_horizontal_axes(self):
        self.controller.scroll(5, -12)
        self.q.CGEventCreateScrollWheelEvent.assert_called_once_with(
            None, self.q.kCGScrollEventUnitPixel, 2, -12, 5,
        )

    def test_unicode_count_is_utf16_units_not_python_codepoints(self):
        self.controller.type_text("a\U0001f600")
        args = self.q.CGEventKeyboardSetUnicodeString.call_args.args
        self.assertEqual(args[1:], (3, "a\U0001f600"))
        self.assertEqual(self.q.CGEventPost.call_count, 2)

    def test_fullscreen_applies_command_and_control_on_both_events(self):
        self.controller.press_key("fullscreen", shortcut=True)
        for call in self.q.CGEventSetFlags.call_args_list:
            self.assertEqual(call.args[1], (1 << 20) | (1 << 18))
        self.assertEqual(self.q.CGEventPost.call_count, 2)

    def test_permission_revocation_rejects_input(self):
        self.q.CGPreflightPostEventAccess.return_value = False
        with self.assertRaisesRegex(RuntimeError, "Accessibility"):
            self.controller.execute({"action": "key", "key": "enter"})
        self.q.CGEventPost.assert_not_called()

    def test_permission_revocation_rejects_state(self):
        self.q.CGPreflightPostEventAccess.return_value = False
        with self.assertRaisesRegex(RuntimeError, "Accessibility"):
            self.controller.state()


if __name__ == "__main__":
    unittest.main()
