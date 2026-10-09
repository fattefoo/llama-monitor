"""Tests for the ``llama-monitor edit`` command."""

import json

import pytest

from llama_monitor import cli


BASE_TEMPLATE = {
    "port": 8080,
    "ctx_size": 32768,
    "cache_type_k": "q8_0",
    "top_k": 20,
    "spec_type": "none",
}


@pytest.fixture
def template_dir(tmp_path, monkeypatch):
    """An isolated templates dir holding a single 'test' template."""
    import llama_monitor.config as config_module

    config_dir = tmp_path / ".config" / "llama-monitor"
    templates_dir = config_dir / "templates"
    templates_dir.mkdir(parents=True)

    monkeypatch.setattr(config_module, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(config_module, "CONFIG_FILE", config_dir / "config.json")
    monkeypatch.setattr(config_module, "TEMPLATES_DIR", templates_dir)

    # validate_template() requires the model and binary to exist.
    model = tmp_path / "test.gguf"
    model.write_text("")
    binary = tmp_path / "llama-server"
    binary.write_text("")
    monkeypatch.setattr("llama_monitor.config._expand_path", lambda p: p)

    template = dict(BASE_TEMPLATE, model_path=str(model), binary=str(binary))
    (templates_dir / "test.json").write_text(json.dumps(template))

    monkeypatch.setattr(cli, "_find_llama_servers", lambda: [])

    return templates_dir


@pytest.fixture
def answers(monkeypatch):
    """Drive the prompts with a scripted list of answers, in order."""
    replies = []

    def fake_input(prompt=""):
        if not replies:
            raise AssertionError(f"unexpected prompt: {prompt!r}")
        return replies.pop(0)

    def fake_yes(prompt="", default=False):
        if not replies:
            return default
        reply = replies.pop(0)
        if reply == "":
            return default
        return str(reply).strip().lower().startswith("y")

    monkeypatch.setattr("builtins.input", fake_input)
    monkeypatch.setattr(cli, "_prompt_yes", fake_yes)
    return lambda *values: replies.extend(values)


def _drop(template_dir, key):
    """Remove a key from the template file, keeping the rest intact."""
    path = template_dir / "test.json"
    template = json.loads(path.read_text())
    del template[key]
    path.write_text(json.dumps(template))


@pytest.fixture
def answers_by_prompt(monkeypatch):
    """Drive the prompts by matching prompt text; unmatched prompts are declined."""
    import re

    rules = {}

    def fake_input(prompt=""):
        for pattern, value in rules.items():
            if re.search(pattern, prompt.strip()):
                return value
        return ""

    def fake_yes(prompt="", default=False):
        for pattern, value in rules.items():
            if re.search(pattern, prompt.strip()):
                return str(value).strip().lower().startswith("y")
        return False   # decline every unmatched group

    monkeypatch.setattr("builtins.input", fake_input)
    monkeypatch.setattr(cli, "_prompt_yes", fake_yes)
    return lambda mapping: rules.update(mapping)


def read(template_dir):
    template = json.loads((template_dir / "test.json").read_text())
    # The binary/model paths are tmpdir-specific; compare the parameters only.
    return {k: v for k, v in template.items()
            if k not in ("name", "model_path", "binary")}


class TestResolveParameter:
    """Parameter names are matched leniently."""

    @pytest.mark.parametrize("name", [
        "cache_type_k",       # template key
        "--cache-type-k",     # flag
        "cache-type-k",       # flag without dashes
        "cachetypek",         # separators ignored for lookups
    ])
    def test_accepted_spellings(self, name):
        spec, note = cli._resolve_parameter(name)
        assert spec is not None and note is None
        assert spec.key == "cache_type_k"

    @pytest.mark.parametrize("name", ["webui", "--webui", "no-webui", "--no-webui"])
    def test_negated_flag_resolves_to_its_parameter(self, name):
        spec, _ = cli._resolve_parameter(name)
        assert spec.key == "webui"

    def test_unknown_name_suggests_a_close_match(self):
        spec, note = cli._resolve_parameter("top_ky")
        assert spec is None
        assert "top_k" in note

    def test_substring_suggestion(self):
        spec, note = cli._resolve_parameter("cache_type")
        assert spec is None
        assert "cache_type_k" in note

    def test_no_suggestions_still_reports_unknown(self):
        spec, note = cli._resolve_parameter("zzzzzzzz")
        assert spec is None
        assert "Unknown parameter." in note


class TestEditSingleParameter:
    """One named parameter is added, changed or removed."""

    def test_change_existing_value(self, template_dir, answers):
        answers("1", "2")          # template 1, cache_type_k -> f16
        cli.cmd_edit("cache_type_k")
        assert read(template_dir)["cache_type_k"] == "f16"

    def test_unchanged_value_can_be_removed(self, template_dir, answers):
        answers("1", "", "y")      # keep current, then remove
        cli.cmd_edit("top_k")
        assert "top_k" not in read(template_dir)

    def test_unchanged_value_is_kept_when_removal_declined(self, template_dir, answers):
        answers("1", "", "n")
        cli.cmd_edit("top_k")
        assert read(template_dir)["top_k"] == 20

    def test_missing_parameter_is_added(self, template_dir, answers):
        answers("1", "4096")
        cli.cmd_edit("batch_size")
        assert read(template_dir)["batch_size"] == 4096

    def test_custom_select_value_keeps_its_numeric_type(self, template_dir, answers):
        answers("1", "34", "262144")    # "custom value...", then the size
        cli.cmd_edit("ctx_size")
        assert read(template_dir)["ctx_size"] == 262144

    def test_default_valued_parameter_offers_to_store_it(self, template_dir, answers):
        answers("1", "", "y")      # accept the default, but store it explicitly
        _drop(template_dir, "top_k")
        cli.cmd_edit("top_k")
        assert read(template_dir)["top_k"] == 40   # llama-server's default

    def test_default_valued_parameter_is_skipped_when_declined(self, template_dir, answers):
        answers("1", "", "n")
        _drop(template_dir, "top_k")
        cli.cmd_edit("top_k")
        assert "top_k" not in read(template_dir)

    def test_unknown_parameter_leaves_template_untouched(self, template_dir, answers):
        answers("1")
        cli.cmd_edit("not_a_parameter")
        assert read(template_dir) == dict(BASE_TEMPLATE)

    def test_inactive_parameter_is_refused(self, template_dir, answers):
        answers("1", "", "", "", "y")   # spec_draft_n_max with spec_type none
        cli.cmd_edit("spec_draft_n_max")
        assert "spec_draft_n_max" not in read(template_dir)

    def test_inactive_parameter_asks_for_its_prerequisite(self, template_dir, answers):
        answers("1", "4", "4")          # spec_type -> draft-mtp, then n_max = 4
        cli.cmd_edit("spec_draft_n_max")
        template = read(template_dir)
        assert template["spec_type"] == "draft-mtp"
        assert template["spec_draft_n_max"] == 4


class TestEditWholeTemplate:
    """``edit`` without a name walks the wizard."""

    def test_walk_updates_and_saves(self, template_dir, answers_by_prompt):
        # Only the Sampling group is entered; top_k is set to 25 there.
        answers_by_prompt({
            r"Select template to edit": "1",
            r"Configure Sampling\?": "y",
            r"^top_k ": "25",
        })
        cli.cmd_edit()
        assert read(template_dir)["top_k"] == 25

    def test_declining_every_group_changes_nothing(self, template_dir, answers_by_prompt):
        answers_by_prompt({r"Select template to edit": "1"})
        cli.cmd_edit()
        assert read(template_dir)["top_k"] == 20

    def test_walk_removes_inapplicable_keys(self, template_dir, answers_by_prompt):
        # spec_draft_n_max with spec_type "none" is dropped on save.
        (template_dir / "test.json").write_text(json.dumps(
            {**json.loads((template_dir / "test.json").read_text()),
             "spec_draft_n_max": 4}))
        answers_by_prompt({r"Select template to edit": "1"})
        cli.cmd_edit()
        assert "spec_draft_n_max" not in read(template_dir)