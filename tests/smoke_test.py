from click.testing import CliRunner

import uminer
from uminer import MinerUClient
from uminer.cli import main


def test_smoke() -> None:
    assert uminer.MinerUClient is MinerUClient

    result = CliRunner().invoke(main, ["--help"])
    assert result.exit_code == 0, result.output
    assert "extract" in result.output
    assert "list" in result.output


if __name__ == "__main__":
    test_smoke()
