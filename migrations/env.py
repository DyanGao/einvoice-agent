from alembic import context
from sqlalchemy import create_engine

from app.db import Base
from app.settings import Settings

target_metadata = Base.metadata


def run_migrations() -> None:
    url = Settings.from_env().database_url
    if context.is_offline_mode():
        context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
        with context.begin_transaction():
            context.run_migrations()
        return
    engine = create_engine(url)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


run_migrations()
