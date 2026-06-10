"""Public construct-and-run API — ``dream.build_harness`` (the SDK entrypoint).

Consumers (chorus/lattice/horizon, evals, examples) must be able to build a
*runnable* Harness from the public surface alone: explicit credentials and
model, no ``DREAM_SMOKE_*`` env coupling, no imports from ``dream.repl``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dream import Harness, SessionOptions, build_harness
from dream.engine._engine import QueryEngine


def _build(tmp_path: Path, **overrides: object) -> Harness:
    kwargs: dict = {
        "model": "test-model",
        "api_key": "test-key",
        "working_dir": tmp_path / "wt",
        "env": {"DREAM_HOME": str(tmp_path / "home")},
    }
    kwargs.update(overrides)
    (tmp_path / "wt").mkdir(parents=True, exist_ok=True)
    return build_harness(**kwargs)


def test_build_harness_is_public() -> None:
    import dream

    assert "build_harness" in dream.__all__


def test_returns_runnable_harness(tmp_path: Path) -> None:
    harness = _build(tmp_path)
    assert isinstance(harness, Harness)
    factory = harness.config._engine_factory
    assert factory is not None
    engine = factory("s_test", SessionOptions())
    assert isinstance(engine, QueryEngine)


def test_no_smoke_env_required(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The public builder must not read DREAM_SMOKE_* at all.
    for var in ("DREAM_SMOKE_API_KEY", "DREAM_SMOKE_MODEL", "DREAM_SMOKE_BASE_URL"):
        monkeypatch.delenv(var, raising=False)
    harness = _build(tmp_path)
    assert harness.config._engine_factory is not None


def test_task_manager_and_cron_registry_wired(tmp_path: Path) -> None:
    from dream.tasks import BackgroundTaskManager

    harness = _build(tmp_path)
    assert isinstance(harness.config.task_manager, BackgroundTaskManager)
    assert isinstance(harness.config.cron_registry_path, Path)


def test_wake_model_override_reaches_wake_streamer(tmp_path: Path) -> None:
    # The heartbeat fires constantly; running it on a cheap model is the
    # single biggest cost lever for an always-on agent. Default: same model.
    harness = _build(tmp_path, wake_model="cheap-mini")
    factory = harness.config.wake_streamer_factory
    assert factory is not None
    assert factory()._model == "cheap-mini"  # type: ignore[attr-defined]

    default = _build(tmp_path, model="main-model")
    default_factory = default.config.wake_streamer_factory
    assert default_factory is not None
    assert default_factory()._model == "main-model"  # type: ignore[attr-defined]


def test_extra_no_longer_smuggles_runtime_fields(tmp_path: Path) -> None:
    # The typed fields replace the ``config.extra`` escape hatch.
    harness = _build(tmp_path)
    assert "task_manager" not in harness.config.extra
    assert "cron_registry_path" not in harness.config.extra


def test_dream_home_override_honoured(tmp_path: Path) -> None:
    # Paths resolved from the provided env mapping, not os.environ.
    _build(tmp_path)
    assert (tmp_path / "home").exists()


def test_empty_model_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="model"):
        _build(tmp_path, model="")


def test_empty_api_key_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="api_key"):
        _build(tmp_path, api_key="")


def test_working_dir_recorded_on_config(tmp_path: Path) -> None:
    harness = _build(tmp_path)
    assert harness.config.working_dir == tmp_path / "wt"


def test_repl_wrapper_still_env_driven(tmp_path: Path) -> None:
    # The REPL convenience keeps its KeyError contract and delegates here.
    from dream.repl._session import build_default_harness

    with pytest.raises(KeyError, match="DREAM_SMOKE"):
        build_default_harness(env={}, working_dir=tmp_path)
