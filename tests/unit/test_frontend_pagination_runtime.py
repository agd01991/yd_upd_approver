import subprocess
from shutil import which


def test_frontend_pagination_runtime_regressions() -> None:
    result = subprocess.run(  # noqa: S603 - fixed local test harness
        [which("node") or "/usr/bin/node", "tests/js/frontend_pagination.test.cjs"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "frontend pagination regressions passed" in result.stdout
