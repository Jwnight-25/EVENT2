"""Coordinate complete backups with API writes and worker computations."""

from contextlib import contextmanager
import fcntl
from .config import DATA_DIR
from .errors import DomainError


@contextmanager
def storage_lock(exclusive=False):
    with open(DATA_DIR / "storage.lock", "a") as handle:
        try:
            fcntl.flock(handle, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        except BlockingIOError:
            raise DomainError("数据备份或计算正在使用存储，请稍后重试", "storage_busy", 409)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
