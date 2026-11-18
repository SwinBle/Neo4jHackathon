from typer.testing import CliRunner

from kidneyloop.cli import app


def test_version_reports_relate_engine():
    result = CliRunner().invoke(app, ["version"])
    assert result.exit_code == 0
    assert "relate 1.1.0" in result.output
