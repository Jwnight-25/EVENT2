import argparse
from alembic.config import Config
from alembic import command
from .config import ROOT


def migrate():
    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["migrate", "backup", "verify-backup"])
    parser.add_argument("--destination")
    args = parser.parse_args()
    if args.action == "migrate":
        migrate()
    else:
        from .backup import backup, verify

        if not args.destination:
            parser.error("需要--destination路径")
        print((backup if args.action == "backup" else verify)(args.destination))
