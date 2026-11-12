"""Data-driven coverage for every entry in params.PARAM_SPECS.

The existing tests in test_params.py cover the table's structural rules and a
few representative specimens. This file walks ALL specs so a new (or mistyped)
table entry fails here, not in production:

- every spec emits its flag with the right value once its ``when`` predicate
  is satisfied (gates derived automatically via ``enabling_values``),
- every bool emits its ``off_flag`` when false (or nothing, without one),
- ``_build_llama_args`` honours every spec's ``when``: an unsatisfiable
  context must keep the flag out of the args,
- every ``when`` predicate survives an empty template without raising.
"""

import pytest

from llama_monitor import params
from llama_monitor.daemon import _build_llama_args

CONFIG = {"default_binary": "/usr/local/bin/llama-server"}

SAMPLE_VALUES = {
    "int": 7,
    "float": 0.75,
    "str": "test-value",
}


def _sample(spec: params.ParamSpec):
    """A valid, non-default-ish value for one spec."""
    if spec.kind == "bool":
        return True
    if spec.kind == "select":
        # Prefer an option that is neither empty nor an explicit off value,
        # so gates such as spec_type actually enable related parameters.
        for value, _display in spec.options:
            if value not in ("", "none", "off", 0, False):
                return value
        for value, _display in spec.options:
            if value != "":
                return value
        pytest.fail(f"{spec.key}: select without a usable option")
    return SAMPLE_VALUES[spec.kind]


def _satisfying_template(spec: params.ParamSpec) -> dict:
    """Template in which spec is active, gates resolved automatically."""
    template = {"model_path": "/m.gguf", spec.key: _sample(spec)}
    for _round in range(3):  # gates may themselves gate (CLI walks depth 3 too)
        if spec.active(template):
            return template
        template.update(params.enabling_values(spec, template))
    return template if spec.active(template) else None


def _args_for(template: dict) -> list:
    return _build_llama_args(template, CONFIG)


@pytest.mark.parametrize("spec", params.PARAM_SPECS, ids=lambda s: s.key)
class TestEverySpec:
    def test_when_predicate_tolerates_empty_template(self, spec):
        """A missing gate key must not raise inside any ``when`` predicate."""
        assert isinstance(spec.active({}), bool)

    def test_flag_emitted_with_satisfied_context(self, spec):
        """Active spec + value present -> flag (and value) in the args."""
        template = _satisfying_template(spec)
        if template is None:
            pytest.fail(
                f"{spec.key}: no automatically derivable context activates "
                "its when predicate — add a manual test or fix the predicate"
            )
        args = _args_for(template)

        if spec.kind == "bool":
            assert spec.flag in args, f"{spec.key}: --{spec.key}-true missing"
        else:
            assert spec.flag in args, f"{spec.key}: flag missing for active spec"
            assert args[args.index(spec.flag) + 1] == str(template[spec.key])

    def test_false_bool_negates(self, spec):
        """False emits the off_flag when the build-table declares one."""
        if spec.kind != "bool":
            pytest.skip("not a bool")
        template = _satisfying_template(spec)
        if template is None:
            pytest.skip(f"{spec.key}: no activatable context")
        template[spec.key] = False
        args = _args_for(template)
        if spec.off_flag:
            assert spec.off_flag in args
        else:
            assert spec.flag not in args
            assert spec.off_flag not in args

    def test_inactive_spec_pruned_from_args(self, spec):
        """A value whose ``when`` context is missing must not reach the flags."""
        if not spec.when:
            pytest.skip("unconditional spec")
        template = {"model_path": "/m.gguf", spec.key: _sample(spec)}
        if spec.active(template):
            pytest.skip(
                f"{spec.key}: active without gates (checked in other tests)"
            )
        args = _args_for(template)
        assert spec.flag not in args
