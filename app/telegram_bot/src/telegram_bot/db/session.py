from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def make_engine(database_url):
    return create_engine(database_url, echo=False, pool_pre_ping=True, future=True)


def make_session_factory(engine):
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


@contextmanager
def get_session(session_factory):
    session = session_factory()
    try:
        yield session
    finally:
        session.close()
