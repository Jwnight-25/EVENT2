import argparse
from alembic.config import Config
from alembic import command
from .config import ROOT


def migrate():
    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["migrate", "backup", "verify-backup", "restore-new"])
    parser.add_argument("--destination")
    parser.add_argument("--source")
    args = parser.parse_args()
    if args.action == "migrate":
        migrate()
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
