import re
from pathlib import Path

import pytest

from kidneyloop import explain, runs, whatif

WEB_QUERIES = Path(__file__).resolve().parents[1] / "web" / "queries.js"

SHARED = {
    "RUN_EXCHANGES": runs.RUN_EXCHANGES,
    "PROFILE": explain.PROFILE,
    "ABO_DONORS": explain.ABO_DONORS,
    "BLOCKING_ANTIGENS": explain.BLOCKING_ANTIGENS,
    "INCOMING": explain.INCOMING,
    "CYCLES_THROUGH": explain.CYCLES_THROUGH,
    "WITHDRAW": whatif.WITHDRAW,
}


def normalize(cypher: str) -> str:
    return " ".join(cypher.split())


def web_queries() -> dict[str, str]:
    text = WEB_QUERIES.read_text(encoding="utf-8")
    return {name: body for name, body in re.findall(r"export const (\w+) = `(.*?)`;", text, re.S)}


@pytest.mark.parametrize("name", sorted(SHARED))
def test_web_page_runs_the_same_cypher_as_the_cli(name):
    assert normalize(web_queries()[name]) == normalize(SHARED[name])
