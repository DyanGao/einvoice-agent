"""The database engine, with a warning when the development password is used
on a database that is not on this machine.

"einvoice" is the password of the local development setup (docker-compose.yml,
.env.example), whose ports are bound to localhost. Anywhere else it is a
known password, so every process that connects says so at startup.
"""

import logging

from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import make_url

logger = logging.getLogger(__name__)

DEVELOPMENT_PASSWORD = "einvoice"  # noqa: S105 -- the known password this module warns about
_LOOPBACK = frozenset({"localhost", "127.0.0.1", "::1"})


def uses_development_password(url: str) -> bool:
    parsed = make_url(url)
    return parsed.password == DEVELOPMENT_PASSWORD and parsed.host not in _LOOPBACK


def database_engine(url: str) -> Engine:
    if uses_development_password(url):
        logger.warning(
            "the database at %s uses the development password; set POSTGRES_PASSWORD "
            "in .env before this installation is reachable by anyone else",
            make_url(url).render_as_string(hide_password=True),
        )
    return create_engine(url)
