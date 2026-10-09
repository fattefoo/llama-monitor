"""Tests for the parameter table and its dependency rules."""

import pytest

from llama_monitor import params
from llama_monitor.daemon import _build_llama_args

CONFIG = {"default_binary": "/usr/local/bin/llama-server"}


class TestTable:
    """The table itself must be well formed."""

    def test_keys_and_flags_unique(self):
        keys = [spec.key for spec in params.PARAM_SPECS]
        flags = [spec.flag for spec in params.PARAM_SPECS]
        assert len(keys) == len(set(keys))
        assert len(flags) == len(set(flags))

    def test_every_select_has_options(self):
        for spec in params.PARAM_SPECS:
            if spec.kind == "select":
                assert spec.options, f"{spec.key} is a select without options"

    def test_every_bool_has_a_flag(self):
        for spec in params.PARAM_SPECS:
            if spec.kind == "bool":
                assert spec.flag.startswith("--")

    def test_off_flag_is_negation(self):
        for spec in params.PARAM_SPECS:
            if spec.off_flag:
                assert spec.off_flag == "--no-" + spec.flag[2:]

    def test_groups_declared_in_order(self):
        assert params.GROUPS == list(dict.fromkeys(s.group for s in params.PARAM_SPECS))


class TestDependencies:
    """A parameter must be inactive when its related value contradicts it."""

    def test_draft_params_inactive_without_spec(self):
        for spec in params.PARAM_SPECS:
            if spec.key.startswith("spec_draft_"):
                assert not spec.active({"spec_type": "none"}), spec.key

    def test_draft_params_active_for_draft_type(self):
        assert params.SPEC_BY_KEY["spec_draft_n_max"].active(
            {"spec_type": "draft-mtp"})

    def test_draft_params_inactive_for_ngram_type(self):
        assert not params.SPEC_BY_KEY["spec_draft_n_max"].active(
            {"spec_type": "ngram-simple"})

    def test_ngram_params_scoped_to_their_type(self):
        simple = params.SPEC_BY_KEY["spec_ngram_simple_size_n"]
        assert simple.active({"spec_type": "ngram-simple"})
        assert not simple.active({"spec_type": "ngram-mod"})
        assert not simple.active({"spec_type": "draft-mtp"})

    def test_spec_type_accepts_comma_separated_list(self):
        assert params.SPEC_BY_KEY["spec_draft_n_max"].active(
            {"spec_type": "ngram-mod,draft-mtp"})
        assert not params.SPEC_BY_KEY["spec_draft_n_max"].active(
            {"spec_type": "ngram-mod,ngram-simple"})

    def test_top_k_inactive_with_mirostat(self):
        assert not params.SPEC_BY_KEY["top_k"].active({"mirostat": 1})
        assert not params.SPEC_BY_KEY["top_p"].active({"mirostat": 2})
        assert params.SPEC_BY_KEY["top_k"].active({"mirostat": 0})

    def test_mirostat_lr_and_ent_scoped_by_version(self):
        lr = params.SPEC_BY_KEY["mirostat_lr"]
        ent = params.SPEC_BY_KEY["mirostat_ent"]
        assert lr.active({"mirostat": 1}) and lr.active({"mirostat": 2})
        assert ent.active({"mirostat": 1})
        assert not ent.active({"mirostat": 2})

    def test_dry_params_need_multiplier(self):
        assert not params.SPEC_BY_KEY["dry_base"].active({"dry_multiplier": 0.0})
        assert params.SPEC_BY_KEY["dry_base"].active({"dry_multiplier": 0.8})

    def test_yarn_params_need_rope_scaling(self):
        assert not params.SPEC_BY_KEY["yarn_orig_ctx"].active({"rope_scaling": "linear"})
        assert params.SPEC_BY_KEY["yarn_orig_ctx"].active({"rope_scaling": "yarn"})

    def test_tensor_split_need_split_mode(self):
        assert not params.SPEC_BY_KEY["tensor_split"].active({"split_mode": "none"})
        assert params.SPEC_BY_KEY["tensor_split"].active({"split_mode": "layer"})

    def test_pooling_needs_embedding(self):
        assert params.SPEC_BY_KEY["pooling"].active({"embedding": True})
        assert not params.SPEC_BY_KEY["pooling"].active({})


class TestBuildArgsWithDependencies:
    """The daemon must not pass flags the template's other values invalidate."""

    def test_draft_n_max_skipped_when_spec_disabled(self):
        template = {
            "model_path": "/m.gguf",
            "spec_type": "none",
            "spec_draft_n_max": 4,
        }
        args = _build_llama_args(template, CONFIG)
        assert "--spec-type" in args
        assert "--spec-draft-n-max" not in args

    def test_draft_n_max_emitted_when_spec_enabled(self):
        template = {
            "model_path": "/m.gguf",
            "spec_type": "draft-mtp",
            "spec_draft_n_max": 4,
        }
        args = _build_llama_args(template, CONFIG)
        assert args[args.index("--spec-draft-n-max") + 1] == "4"

    def test_top_k_skipped_with_mirostat(self):
        args = _build_llama_args(
            {"model_path": "/m.gguf", "mirostat": 1, "top_k": 20}, CONFIG)
        assert "--top-k" not in args
        assert "--mirostat" in args

    def test_false_bool_emits_negated_flag(self):
        args = _build_llama_args(
            {"model_path": "/m.gguf", "webui": False}, CONFIG)
        assert "--no-webui" in args

    def test_true_bool_emits_plain_flag(self):
        args = _build_llama_args({"model_path": "/m.gguf", "metrics": True}, CONFIG)
        assert "--metrics" in args

    def test_bool_without_negation_is_omitted(self):
        args = _build_llama_args({"model_path": "/m.gguf", "metrics": False}, CONFIG)
        assert "--metrics" not in args and "--no-metrics" not in args

    def test_empty_string_values_are_omitted(self):
        args = _build_llama_args(
            {"model_path": "/m.gguf", "lora": "", "api_key": ""}, CONFIG)
        assert "--lora" not in args and "--api-key" not in args

    def test_port_and_bind_always_passed(self):
        args = _build_llama_args({"model_path": "/m.gguf"}, CONFIG)
        assert args[args.index("--port") + 1] == "8080"
        assert args[args.index("--host") + 1] == "127.0.0.1"


class TestPruneDefaults:
    """Values equal to llama-server's default should not clutter templates."""

    def test_is_default_detects_matching_value(self):
        spec = params.SPEC_BY_KEY["cache_type_k"]
        assert params.is_default(spec, "f16")
        assert not params.is_default(spec, "q8_0")

    def test_empty_value_counts_as_default(self):
        assert params.is_default(params.SPEC_BY_KEY["lora"], "")

    def test_prune_removes_defaults_and_inactive_keys(self):
        template = {
            "cache_type_k": "f16",       # default
            "temperature": 0.8,          # default
            "top_k": 20,                 # not default
            "spec_type": "none",
            "spec_draft_n_max": 4,       # inactive
            "lora": "",
        }
        params.prune_defaults(template)
        assert template == {"top_k": 20}