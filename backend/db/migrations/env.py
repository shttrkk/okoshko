import os

from alembic import context
from sqlalchemy import create_engine

url = os.getenv("DB_URL", "postgresql://okno:okno@db:5432/okno")
url = url.replace("postgresql://", "postgresql+psycopg://", 1)


def run():
    if context.is_offline_mode():
        context.configure(url=url, literal_binds=True)
        with context.begin_transaction():
            context.run_migrations()
    else:
        with create_engine(url).connect() as connection:
            context.configure(connection=connection)
            with context.begin_transaction():
                context.run_migrations()


run()

