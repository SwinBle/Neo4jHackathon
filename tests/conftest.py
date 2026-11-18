import relate
import pytest

from kidneyloop.model import Pool
from kidneyloop.store import derive, load_pool


@pytest.fixture
def graph_of():
    opened = []

    def build(patients, donors):
        conn = relate.connect(":memory:")
        load_pool(conn, Pool(tuple(patients), tuple(donors)))
        derive(conn)
        opened.append(conn)
        return conn

    yield build
    for conn in opened:
        conn.close()
