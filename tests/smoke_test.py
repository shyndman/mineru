from importlib.metadata import version

from click.testing import CliRunner

import uminer
from uminer import MinerUClient
from uminer.cli import main


def smoke_test() -> None:
    assert version("uminer")
    assert uminer.MinerUClient is MinerUClient

    result = CliRunner().invoke(main, ["--help"])
    assert result.exit_code == 0, result.output
    assert "extract" in result.output
    assert "list" in result.output


if __name__ == "__main__":
    smoke_test()
