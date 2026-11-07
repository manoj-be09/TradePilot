"""Create, list, arm, disarm or revoke a signed live release (LIVE_TRADING.md L5 locks).

The signing key is read from LIVE_RELEASE_SIGNING_KEY in this one command's environment
(at least 32 characters) and never stored. Live trading also stays locked by the config
flag and the kill switch; a release alone sends nothing.

  PYTHONPATH=. python scripts/live-release.py list
  LIVE_RELEASE_SIGNING_KEY=... PYTHONPATH=. python scripts/live-release.py create \
      --owner-email you@example.com --strategy long_straddle --max-lots 1 \
      --max-trade-loss 20000 --daily-loss-cap 25000 --valid-until 2027-01-31
  PYTHONPATH=. python scripts/live-release.py arm|disarm|revoke RELEASE_ID --reason "..."
"""

import argparse
import getpass
import os
import sys
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select

from apps.api.settings import get_settings
from packages.broker_upstox.releases import create_release, revoke_release, set_armed
from packages.database.models import LiveRelease, User
from packages.database.session import build_session_factory


def main() -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list")
    create = commands.add_parser("create")
    create.add_argument("--owner-email", required=True)
    create.add_argument("--strategy", required=True)
    create.add_argument("--max-lots", type=int, required=True)
    create.add_argument("--max-trade-loss", type=Decimal, required=True)
    create.add_argument("--daily-loss-cap", type=Decimal, required=True)
    create.add_argument("--valid-until", type=date.fromisoformat, required=True)
    for name in ("arm", "disarm", "revoke"):
        command = commands.add_parser(name)
        command.add_argument("release_id", type=UUID)
        command.add_argument("--reason", required=name != "revoke", default="")
    args = parser.parse_args()

    with build_session_factory(get_settings().database_url)() as session:
        if args.command == "list":
            for release in session.scalars(select(LiveRelease).order_by(LiveRelease.created_at)):
                state = "revoked" if release.revoked_at else "active"
                print(release.id, release.strategy_key, f"{release.max_lots} lot(s)",
                      f"trade loss {release.max_trade_loss}", f"day cap {release.daily_loss_cap}",
                      f"until {release.valid_until}", state)
        elif args.command == "create":
            key = os.environ.get("LIVE_RELEASE_SIGNING_KEY", "")
            owner = session.scalar(select(User.id).where(User.email == args.owner_email.lower()))
            if owner is None:
                print("No user with that email", file=sys.stderr)
                return 2
            release = create_release(
                session, key=key, owner_user_id=owner, strategy_key=args.strategy,
                max_lots=args.max_lots, max_trade_loss=args.max_trade_loss,
                daily_loss_cap=args.daily_loss_cap, valid_until=args.valid_until,
            )
            print("Created release", release.id, "(disarmed)")
        elif args.command == "revoke":
            revoke_release(session, args.release_id)
            print("Revoked", args.release_id)
        else:
            set_armed(session, args.release_id, armed=args.command == "arm",
                      actor=getpass.getuser(), reason=args.reason)
            print(args.command.capitalize() + "ed", args.release_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
