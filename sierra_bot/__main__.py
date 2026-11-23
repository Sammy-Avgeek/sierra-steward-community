import logging
import sqlite3

import discord

from .bot import SierraBot
from .config import ConfigError, Settings

logger = logging.getLogger("sierra_bot")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        settings, token = Settings.from_env()
        SierraBot(settings).run(token, log_handler=None)
    except ConfigError as error:
        logger.error("%s", error)
    except discord.LoginFailure:
        logger.error("Discord rejected DISCORD_TOKEN. Use the token from the application's Bot page.")
    except discord.PrivilegedIntentsRequired:
        logger.error("Enable Server Members Intent on the application's Bot page.")
    except discord.HTTPException as error:
        logger.error(
            "Startup Discord request failed (status %s, code %s). Check the server ID and app installation.",
            error.status,
            error.code,
        )
    except (OSError, sqlite3.Error):
        logger.exception("Startup failed. Check network connectivity and the SQLite file path.")
    else:
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
