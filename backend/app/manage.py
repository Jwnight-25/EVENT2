import argparse
from alembic.config import Config
from alembic import command
from .config import ROOT


def migrate():
    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["migrate"])
    parser.parse_args()
    migrate()
