from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from shutil import which

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
HARNESS = REPOSITORY_ROOT / "tests/js/frontend_pagination.test.cjs"


def run_frontend_harness(node: str, *, timeout: float = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - executable was resolved through PATH
        [node, str(HARNESS)],
        check=False,
        capture_output=True,
        text=True,
        cwd=REPOSITORY_ROOT,
        timeout=timeout,
    )


def test_frontend_pagination_runtime_regressions() -> None:
    node = which("node")
    if node is None:
        pytest.skip("Node.js is required for frontend runtime tests; install Node.js 22.x")
    result = run_frontend_harness(node)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "frontend pagination regressions passed" in result.stdout


def test_launcher_uses_node_from_nonstandard_path(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):
        captured.update(command=command, kwargs=kwargs)
        return subprocess.CompletedProcess(command, 0, "ok", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    run_frontend_harness("/custom/node")
    assert captured["command"] == ["/custom/node", str(HARNESS)]
    assert captured["kwargs"]["cwd"] == REPOSITORY_ROOT  # type: ignore[index]


def test_runtime_test_skips_with_install_hint_when_node_is_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys.modules[__name__], "which", lambda _: None)
    with pytest.raises(pytest.skip.Exception, match="install Node.js 22.x"):
        test_frontend_pagination_runtime_regressions()


@pytest.mark.parametrize("failure", ["exit", "timeout"])
def test_launcher_does_not_turn_runtime_failures_into_skip(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    if failure == "exit":
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 7, "", "failed"),
        )
        assert run_frontend_harness("node").returncode == 7
    else:
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                subprocess.TimeoutExpired(args[0], kwargs["timeout"])
            ),
        )
        with pytest.raises(subprocess.TimeoutExpired):
            run_frontend_harness("node", timeout=1)
