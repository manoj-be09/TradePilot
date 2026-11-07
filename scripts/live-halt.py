"""Show, set or release the live kill switch. Nothing can trade live yet; this is the flag.

Usage:
  PYTHONPATH=. python scripts/live-halt.py                     # show
  PYTHONPATH=. python scripts/live-halt.py --halt "reason"
  PYTHONPATH=. python scripts/live-halt.py --release "reason" --confirm RELEASE

With no recorded event, or if the table cannot be read, live trading counts as halted.
"""

import argparse
import getpass
import sys

from apps.api.settings import get_settings
from packages.broker_upstox.halt import live_halted, set_live_halt
from packages.database.session import build_session_factory


def main() -> int:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--halt", metavar="REASON")
    group.add_argument("--release", metavar="REASON")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()
    with build_session_factory(get_settings().database_url)() as session:
        if args.halt:
            set_live_halt(session, halted=True, actor=getpass.getuser(), reason=args.halt)
        elif args.release:
            if args.confirm != "RELEASE":
                print("Releasing needs --confirm RELEASE", file=sys.stderr)
                return 2
            set_live_halt(session, halted=False, actor=getpass.getuser(), reason=args.release)
        print("Live trading: HALTED" if live_halted(session) else "Live trading: released")
    return 0


if __name__ == "__main__":
    sys.exit(main())
