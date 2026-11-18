import relate
import pytest

from kidneyloop.model import Donor, Patient, Pool
from kidneyloop.store import derive, load_pool


def patient(id, blood="O", antibodies=(), wait_days=100):
    return Patient(id, blood, frozenset(antibodies), wait_days)


def donor(id, blood="O", antigens=("A1",), paired_with=None):
    return Donor(id, blood, frozenset(antigens), paired_with)


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
