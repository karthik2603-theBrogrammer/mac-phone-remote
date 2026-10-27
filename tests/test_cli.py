import contextlib
import io
import unittest
from unittest.mock import patch

from mac_phone_remote.__main__ import main


class CLITests(unittest.TestCase):
    def test_real_control_requires_explicit_public_url_before_permission_check(self):
        error = io.StringIO()
        with patch("sys.argv", ["mac-phone-remote"]), patch(
            "mac_phone_remote.__main__.MacController"
        ) as controller, contextlib.redirect_stderr(error):
            with self.assertRaises(SystemExit) as result:
                main()
        self.assertEqual(result.exception.code, 2)
        controller.assert_not_called()
        self.assertIn("--public-url", error.getvalue())

    def test_demo_can_start_without_network_or_real_input(self):
        output = io.StringIO()
        with patch("sys.argv", ["mac-phone-remote", "--demo"]), patch(
            "mac_phone_remote.__main__.RemoteServer"
        ) as server_type, contextlib.redirect_stdout(output):
            server = server_type.return_value
            server.serve_forever.side_effect = KeyboardInterrupt
            main()
        self.assertTrue(server_type.call_args.args[1].demo)
        self.assertIn("single use", output.getvalue())
        self.assertIn("1 hour", output.getvalue())
        server.server_close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
