import argparse
import logging
import sys

from .backend import DemoController, MacController
from .server import RemoteServer


def main():
    parser = argparse.ArgumentParser(description="Touch controls for your Mac, privately over Tailscale.")
    parser.add_argument("--port", type=int, default=8765, help="Loopback port (default: 8765)")
    parser.add_argument("--public-url", help="Exact Tailscale HTTPS origin, with no path")
    parser.add_argument("--demo", action="store_true", help="Preview with fake displays; no host input")
    parser.add_argument("--request-permission", action="store_true", help="Request macOS Accessibility access and exit")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    if args.request_permission:
        if sys.platform != "darwin":
            parser.error("Accessibility permissions can only be requested on macOS.")
        import Quartz
        granted = Quartz.CGRequestPostEventAccess()
        print("Accessibility access granted." if granted else
              "Enable your terminal/Python in System Settings > Privacy & Security > Accessibility, "
              "then quit and reopen the terminal.")
        return
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
    try:
        origin = args.public_url or f"http://127.0.0.1:{args.port}"
        if not args.demo and not args.public_url:
            parser.error("--public-url with your Tailscale HTTPS origin is required for real Mac control.")
        controller = DemoController() if args.demo else MacController()
        server = RemoteServer(("127.0.0.1", args.port), controller, origin)
    except (RuntimeError, ValueError, OSError) as exc:
        parser.exit(1, f"Cannot start remote: {exc}\n")
    print(f"\n{'DEMO ONLY - no computer control' if args.demo else 'Mac Phone Remote'}")
    print(f"Open on your phone: {server.origin}")
    print(f"Pairing code: {server.auth.code} (single use; expires in 10 minutes)")
    print("One pairing per run; sessions last 1 hour and are bound to your Tailscale user.")
    print("Restart to pair again or revoke the current session.")
    if not args.public_url:
        print("Local preview only. Configure Tailscale Serve and --public-url for iPhone access.")
    print("Keep this terminal open. Press Ctrl+C to stop.\n", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nRemote stopped; all sessions revoked.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
