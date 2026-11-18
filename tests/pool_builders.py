from kidneyloop.model import Donor, Patient


def patient(id, blood="O", antibodies=(), wait_days=100):
    return Patient(id, blood, frozenset(antibodies), wait_days)


def donor(id, blood="O", antigens=("A1",), paired_with=None):
    return Donor(id, blood, frozenset(antigens), paired_with)
