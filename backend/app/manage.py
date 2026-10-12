import argparse
from alembic.config import Config
from alembic import command
from .config import ROOT


def migrate():
    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action", choices=["migrate", "backup", "verify-backup", "restore-new", "update-market"]
    )
    parser.add_argument("--destination")
    parser.add_argument("--source")
    parser.add_argument("--stock-id")
    parser.add_argument("--retry", action="store_true", help="retry a pending daily close check immediately")
    args = parser.parse_args()
    if args.action == "migrate":
        migrate()
    elif args.action == "update-market":
        import json
        from .market_updates import update_due
        from .errors import DomainError

        try:
            print(json.dumps(update_due(args.stock_id, force=args.retry), ensure_ascii=False))
        except DomainError as exc:
            print(json.dumps({"code": exc.code, "message": exc.message}, ensure_ascii=False))
            raise SystemExit(0 if exc.code in ("storage_busy", "update_busy") else 1)
    else:
        from .backup import backup, verify, restore_new

        if not args.destination:
            parser.error("需要--destination路径")
        if args.action == "restore-new":
            if not args.source:
                parser.error("恢复需要--source备份路径")
            print(restore_new(args.source, args.destination))
        else:
            print((backup if args.action == "backup" else verify)(args.destination))
