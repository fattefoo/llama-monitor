"""Tests for the wizard's spec adaptation and template finalization."""

import pytest

from llama_monitor import cli, params


HELP = """\
common params:
-c,    --ctx-size N                     size of the prompt context (default: 0)
-fa,   --flash-attn [on|off|auto]       set Flash Attention use
-ctk,  --cache-type-k TYPE              KV cache data type for K
                                        allowed values: f32, f16, q8_0
                                        (default: f16)
-lm,   --load-mode MODE                 model loading mode (default: auto)
                                        - auto: mmap, unless unsupported
                                        - mmap: memory-map model
                                        - mmap+mlock: mmap + keep in RAM
-ui,   --webui, --no-webui              whether to enable the Web UI
--metrics                              enable metrics endpoint
--spec-type none,draft-mtp,ngram-simple types of speculative decoding
"""


@pytest.fixture
def specs():
    """PARAM_SPECS restricted to what the fake help text advertises."""
    cli._HELP_CACHE["/fake/llama-server"] = HELP
    result = cli._runtime_specs("/fake/llama-server")
    cli._HELP_CACHE.pop("/fake/llama-server")
    return result


def by_key(spec_list):
    return {spec.key: spec for spec in spec_list}


class TestRuntimeSpecs:
    """--help introspection narrows the table to the selected build."""

    def test_unknown_flags_are_dropped(self, specs):
        keys = by_key(specs)
        assert "port" not in keys          # --port not in the fake help
        assert "ctx_size" in keys

    def test_inline_values_parsed(self, specs):
        assert [v for v, _ in by_key(specs)["flash_attn"].options] == [
            "on", "off", "auto"]

    def test_allowed_values_parsed(self, specs):
        assert [v for v, _ in by_key(specs)["cache_type_k"].options] == [
            "f32", "f16", "q8_0"]

    def test_bullet_values_parsed(self, specs):
        values = [v for v, _ in by_key(specs)["load_mode"].options]
        assert values == ["", "auto", "mmap", "mmap+mlock"]

    def test_bare_values_parsed(self, specs):
        assert [v for v, _ in by_key(specs)["spec_type"].options] == [
            "none", "draft-mtp", "ngram-simple"]

    def test_unset_entry_is_kept(self, specs):
        assert by_key(specs)["load_mode"].options[0][0] == ""

    def test_off_flag_dropped_when_build_lacks_it(self, specs):
        # --metrics has no --no-metrics form in the fake help.
        assert by_key(specs)["metrics"].off_flag is None
        assert by_key(specs)["webui"].off_flag == "--no-webui"

    def test_specs_without_help_value_list_keep_static_options(self, specs):
        assert [v for v, _ in by_key(specs)["ctx_size"].options][:3] == [
            0, 8192, 16384]

    def test_unqueryable_binary_falls_back_to_full_table(self):
        cli._HELP_CACHE["/fake/broken"] = ""
        try:
            assert cli._runtime_specs("/fake/broken") == params.PARAM_SPECS
        finally:
            cli._HELP_CACHE.pop("/fake/broken")

    def test_no_binary_falls_back_to_full_table(self):
        assert cli._runtime_specs(None) == params.PARAM_SPECS


class TestFinalizeTemplate:
    """Irrelevant and default-valued keys are cleaned up before saving."""

    def _finalize(self, template, answers=("", "")):
        inputs = iter(answers)
        original = cli._prompt_yes
        def fake_yes(prompt, default=False):
            value = next(inputs, "y" if default else "n")
            return str(value).strip().lower().startswith("y") if value else default

        cli._prompt_yes = fake_yes
        try:
            cli._finalize_template(template, params.PARAM_SPECS,
                                   params.PARAM_FLAGS, params.SPEC_BY_KEY,
                                   params.is_default)
        finally:
            cli._prompt_yes = original
        return template

    def test_inactive_keys_are_removed(self):
        template = {"spec_type": "none", "spec_draft_n_max": 4}
        # spec_type "none" is also llama-server's default, so both go.
        assert self._finalize(template) == {}

    def test_inactive_removed_but_non_default_kept(self):
        template = {"spec_type": "ngram-mod", "spec_draft_n_max": 4}
        assert self._finalize(template) == {"spec_type": "ngram-mod"}

    def test_defaults_are_dropped(self):
        template = {"cache_type_k": "f16", "temperature": 0.8, "top_k": 20}
        result = self._finalize(template)
        assert result == {"top_k": 20}

    def test_defaults_kept_when_declined(self):
        template = {"cache_type_k": "f16", "top_k": 20}
        result = self._finalize(template, answers=("n",))
        assert result == {"cache_type_k": "f16", "top_k": 20}

    def test_always_passed_keys_are_never_pruned(self):
        template = {"port": 8080, "bind": "127.0.0.1", "name": "x",
                    "model_path": "/m.gguf", "binary": "/b"}
        assert self._finalize(template) == template

    def test_legacy_chat_format_is_migrated(self):
        template = {"chat_format": "chatml"}
        assert self._finalize(template) == {"chat_template": "chatml"}

    def test_explicit_chat_template_wins_over_legacy(self):
        template = {"chat_format": "chatml", "chat_template": "gemma"}
        result = self._finalize(template)
        assert result == {"chat_template": "gemma"}