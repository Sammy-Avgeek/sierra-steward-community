import errno
import logging
import os
import sqlite3
from contextlib import contextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path

import discord

from .config import ConfigError

LOG_FORMAT = "%(asctime)s %(levelname)s %(message)s"


class AlreadyRunningError(RuntimeError):
    """Another copy is using this installation."""


@contextmanager
def installation_lock(directory: Path):
    # The OS releases the lock on exit. Keep the file so two processes always lock the same file.
    with (directory / ".sierra-steward.lock").open("a+b") as handle:
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            if error.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                raise AlreadyRunningError("The bot is already running with these settings.") from error
            raise
        yield


def configure_logging(directory: Path, handlers: list[logging.Handler]) -> None:
    try:
        directory.mkdir(parents=True, exist_ok=True)
        handlers.append(
            RotatingFileHandler(
                directory / "sierra-steward.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
            )
        )
    except OSError:
        logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, handlers=handlers, force=True)
        logging.getLogger(__name__).warning("Could not write log files in %s", directory)
    else:
        logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, handlers=handlers, force=True)
    logging.getLogger("discord.client").setLevel(logging.ERROR)
    logging.getLogger("discord.gateway").setLevel(logging.WARNING)


def log_startup_error(logger: logging.Logger, error: Exception) -> str:
    if isinstance(error, ConfigError):
        message = str(error)
    elif isinstance(error, discord.LoginFailure):
        message = "Discord rejected DISCORD_TOKEN. Use the token from the application's Bot page."
    elif isinstance(error, discord.PrivilegedIntentsRequired):
        message = "Enable Server Members Intent on the application's Bot page."
    elif isinstance(error, discord.HTTPException):
        message = (
            f"Discord returned HTTP {error.status} (code {error.code}). "
            "Check that the app is installed and the server ID is correct."
        )
    elif isinstance(error, sqlite3.Error):
        message = "Could not open the warning database. Check its path and write permissions."
    elif isinstance(error, OSError):
        message = "Startup failed. Check network connectivity and local file permissions."
    else:
        message = "An unexpected error stopped the bot. Check the activity log."
    logger.error(message, exc_info=not isinstance(error, (ConfigError, discord.DiscordException)))
    return message
