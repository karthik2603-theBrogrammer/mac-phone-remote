import sys
import unittest


@unittest.skipUnless(sys.platform == "darwin", "Requires real macOS Quartz bindings")
class MacOSBindingTests(unittest.TestCase):
    def test_required_quartz_exports_exist(self):
        import Quartz
        for name in (
            "CGPreflightPostEventAccess", "CGRequestPostEventAccess",
            "CGGetActiveDisplayList", "CGDisplayMirrorsDisplay", "CGDisplayBounds",
            "CGEventCreateMouseEvent", "CGEventCreateScrollWheelEvent",
            "CGEventCreateKeyboardEvent", "CGEventKeyboardSetUnicodeString",
            "CGEventSetIntegerValueField", "CGEventSetFlags", "CGEventPost",
            "kCGNullDirectDisplay", "kCGEventFlagMaskCommand", "kCGEventFlagMaskControl",
        ):
            with self.subTest(name=name):
                self.assertTrue(hasattr(Quartz, name), f"Missing Quartz export: {name}")

    def test_unicode_binding_accepts_utf16_count_without_posting_input(self):
        import Quartz
        event = Quartz.CGEventCreateKeyboardEvent(None, 0, True)
        if event is None:
            self.skipTest("Runner cannot create keyboard events without a GUI session")
        # Construct only; never inject input into a CI machine's desktop.
        Quartz.CGEventKeyboardSetUnicodeString(event, 3, "a\U0001f600")


if __name__ == "__main__":
    unittest.main()
