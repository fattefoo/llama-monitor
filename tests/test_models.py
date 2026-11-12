"""Tests for the `models` subcommand (GGUF files without templates).

Regression: _models_with_templates() called list_template_names() without a
local import, so `llama-monitor models` died with
"Error: name 'list_template_names' is not defined".

Note: validate_template() requires model_path to exist on disk, so the
fixtures create real files and reference full paths.
"""

import pytest

from llama_monitor import cli


def _make_gguf(models_dir, name):
    path = models_dir / name
    path.write_bytes(b"\x00" * 16)
    return path


@pytest.fixture
def fake_models_dir(tmp_path, monkeypatch):
    """Point ~/models at a tmp dir (cli._get_model_files uses expanduser)."""
    models_dir = tmp_path / "models"
    models_dir.mkdir()
    monkeypatch.setenv("HOME", str(tmp_path))
    return models_dir


def _write_template(templates_dir, name, model_path):
    """Write a minimal loadable template for an existing model file."""
    (templates_dir / f"{name}.json").write_text(
        '{"model_path": "%s", "port": 8080}' % model_path
    )


def test_models_lists_missing(fake_models_dir, isolated_template_dir, capsys):
    """A model with a template is hidden, the uncovered one is listed."""
    templates_dir, _ = isolated_template_dir
    templated = _make_gguf(fake_models_dir, "templated.gguf")
    _make_gguf(fake_models_dir, "missing.gguf")

    _write_template(templates_dir, "templated", templated)

    cli.cmd_models()

    out = capsys.readouterr().out
    assert "missing.gguf" in out
    assert "templated.gguf" not in out


def test_models_all_covered(fake_models_dir, isolated_template_dir, capsys):
    templates_dir, _ = isolated_template_dir
    templated = _make_gguf(fake_models_dir, "templated.gguf")

    _write_template(templates_dir, "templated", templated)

    cli.cmd_models()

    out = capsys.readouterr().out
    assert "All models already have templates." in out


def test_models_with_templates_basenames(fake_models_dir, isolated_template_dir):
    """_models_with_templates returns basenames of loadable templates.

    Templates that fail to load (e.g. broken JSON) are skipped silently.
    """
    templates_dir, _ = isolated_template_dir
    real = _make_gguf(fake_models_dir, "model-a.gguf")
    _write_template(templates_dir, "a", real)
    # Unloadable template must be skipped, not raise.
    (templates_dir / "broken.json").write_text("{not json")

    assert cli._models_with_templates() == {"model-a.gguf"}
