"""Tests for llama-monitor daemon module."""

import json
import os
from pathlib import Path

import pytest

from llama_monitor.daemon import _build_llama_args, _add_kv


class TestBuildLlamaArgs:
    """Test llama-server argument building from templates."""
    
    def test_minimal_template(self):
        """Test building args with minimal template (model only)."""
        config = {"default_binary": "/usr/local/bin/llama-server"}
        template = {
            "model_path": "/path/to/model.gguf",
        }
        args = _build_llama_args(template, config)
        
        assert args[0] == "/usr/local/bin/llama-server"
        assert args[1] == "--model"
        assert args[2] == "/path/to/model.gguf"
        assert args[3] == "--host"
        assert args[4] == "127.0.0.1"
        assert args[5] == "--port"
        assert args[6] == "8080"
    
    def test_key_value_params(self):
        """Test all key-value parameters are properly converted."""
        config = {"default_binary": "/usr/local/bin/llama-server"}
        template = {
            "model_path": "/path/to/model.gguf",
            "ctx_size": 4096,
            "n_gpu_layers": 35,
            "n_cpu_moe": 41,
            "parallel": 1,
            "spec_type": "draft-mtp",
            "spec_draft_n_max": 2,
            "cache_type_k": "turbo4",
            "cache_type_v": "turbo3",
            "chat_format": "chatml",
            "temperature": 0.8,
            "top_p": 0.95,
            "top_k": 20,
            "min_p": 0.01,
            "seed": 42,
        }
        args = _build_llama_args(template, config)
        
        # Check key-value pairs are present
        assert "--ctx-size" in args
        assert "4096" in args[args.index("--ctx-size") + 1]
        assert "--n-gpu-layers" in args
        assert "35" in args[args.index("--n-gpu-layers") + 1]
        assert "--n-cpu-moe" in args
        assert "41" in args[args.index("--n-cpu-moe") + 1]
        assert "--parallel" in args
        assert "1" in args[args.index("--parallel") + 1]
        assert "--spec-type" in args
        assert "draft-mtp" in args[args.index("--spec-type") + 1]
        assert "--spec-draft-n-max" in args
        assert "2" in args[args.index("--spec-draft-n-max") + 1]
        assert "--cache-type-k" in args
        assert "turbo4" in args[args.index("--cache-type-k") + 1]
        assert "--cache-type-v" in args
        assert "turbo3" in args[args.index("--cache-type-v") + 1]
        assert "--chat-format" in args
        assert "chatml" in args[args.index("--chat-format") + 1]
        assert "--temp" in args
        assert "0.8" in args[args.index("--temp") + 1]
        assert "--top-p" in args
        assert "0.95" in args[args.index("--top-p") + 1]
        assert "--top-k" in args
        assert "20" in args[args.index("--top-k") + 1]
        assert "--min-p" in args
        assert "0.01" in args[args.index("--min-p") + 1]
        assert "--seed" in args
        assert "42" in args[args.index("--seed") + 1]
    
    def test_flash_attn_as_kv(self):
        """Test flash_attn is sent as key-value parameter."""
        config = {"default_binary": "/usr/local/bin/llama-server"}
        template = {
            "model_path": "/path/to/model.gguf",
            "flash_attn": "on",
        }
        args = _build_llama_args(template, config)
        
        assert "--flash-attn" in args
        assert "on" in args[args.index("--flash-attn") + 1]
    
    def test_flash_attn_disabled(self):
        """Test that flash_attn: 'off' sends --flash-attn off."""
        config = {"default_binary": "/usr/local/bin/llama-server"}
        template = {
            "model_path": "/path/to/model.gguf",
            "flash_attn": "off",
        }
        args = _build_llama_args(template, config)
        
        assert "--flash-attn" in args
        assert "off" in args[args.index("--flash-attn") + 1]
    
    def test_boolean_flags(self):
        """Test boolean flags are added correctly."""
        config = {"default_binary": "/usr/local/bin/llama-server"}
        template = {
            "model_path": "/path/to/model.gguf",
            "load_mode": "mmap+mlock",
            "jinja": True,
        }
        args = _build_llama_args(template, config)
        
        assert "--load-mode" in args
        assert "mmap+mlock" in args[args.index("--load-mode") + 1]
        assert "--jinja" in args
    
    def test_boolean_flags_false(self):
        """Test false boolean flags are NOT added."""
        config = {"default_binary": "/usr/local/bin/llama-server"}
        template = {
            "model_path": "/path/to/model.gguf",
            "jinja": False,
        }
        args = _build_llama_args(template, config)
        
        assert "--load-mode" not in args
        assert "--jinja" not in args
    
    def test_boolean_flags_missing(self):
        """Test missing boolean flags are NOT added."""
        config = {"default_binary": "/usr/local/bin/llama-server"}
        template = {
            "model_path": "/path/to/model.gguf",
        }
        args = _build_llama_args(template, config)
        
        assert "--load-mode" not in args
        assert "--jinja" not in args
    
    def test_custom_binary(self):
        """Test custom binary path from template."""
        config = {"default_binary": "/usr/local/bin/llama-server"}
        template = {
            "model_path": "/path/to/model.gguf",
            "binary": "/home/user/custom/llama-server",
        }
        args = _build_llama_args(template, config)
        assert args[0] == "/home/user/custom/llama-server"
    
    def test_fallback_binary(self):
        """Test fallback to default_binary from config."""
        config = {"default_binary": "/usr/local/bin/llama-server"}
        template = {
            "model_path": "/path/to/model.gguf",
        }
        args = _build_llama_args(template, config)
        assert args[0] == "/usr/local/bin/llama-server"
    
    def test_custom_port(self):
        """Test custom port from template."""
        config = {"default_binary": "/usr/local/bin/llama-server"}
        template = {
            "model_path": "/path/to/model.gguf",
            "port": 9000,
        }
        args = _build_llama_args(template, config)
        assert "--port" in args
        assert "9000" in args[args.index("--port") + 1]
    
    def test_custom_bind(self):
        """Test custom bind address from template."""
        config = {"default_binary": "/usr/local/bin/llama-server"}
        template = {
            "model_path": "/path/to/model.gguf",
            "bind": "0.0.0.0",
        }
        args = _build_llama_args(template, config)
        assert "--host" in args
        assert "0.0.0.0" in args[args.index("--host") + 1]
    
    def test_additional_kv_params(self):
        """Test additional key-value params like logit_bias, penalties, etc."""
        config = {"default_binary": "/usr/local/bin/llama-server"}
        template = {
            "model_path": "/path/to/model.gguf",
            "logit_bias": '{"1234": -1.0}',
            "penalty_last_n": 64,
            "penalty_repeat": 1.0,
            "penalty_freq": 0.5,
            "penalty_present": 0.3,
            "mirostat": 2,
            "mirostat_tau": 5.0,
            "mirostat_eta": 0.1,
            "split_mode": 1,
            "row_split": 1,
            "tensor_split": "[0.5, 0.5]",
            "lora_base": "/path/to/lora-base",
            "lora_path": "/path/to/lora",
            "num_gpu": 2,
            "rpc": "tcp://localhost:5555",
        }
        args = _build_llama_args(template, config)
        
        assert "--logit-bias" in args
        assert "--penalty-last-n" in args
        assert "--penalty-repeat" in args
        assert "--penalty-freq" in args
        assert "--penalty-penalty" in args  # Note: --penalty-present → --penalty-penalty
        assert "--mirostat" in args
        assert "--mirostat-tau" in args
        assert "--mirostat-eta" in args
        assert "--split-mode" in args
        assert "--row-split" in args
        assert "--tensor-split" in args
        assert "--lora-base" in args
        assert "--lora-path" in args
        assert "--num-gpu" in args
        assert "--rpc" in args


class TestAddKv:
    """Test the _add_kv helper function."""
    
    def test_add_kv_present(self):
        """Test adding key-value arg when key exists."""
        args = []
        template = {"some_key": "value"}
        _add_kv(args, template, "some_key", "--some-key")
        assert args == ["--some-key", "value"]
    
    def test_add_kv_missing(self):
        """Test not adding key-value arg when key missing."""
        args = []
        template = {"other_key": "value"}
        _add_kv(args, template, "some_key", "--some-key")
        assert args == []
    
    def test_add_kv_value_types(self):
        """Test that values are converted to strings."""
        args = []
        template = {"key_int": 42, "key_float": 3.14, "key_bool": True}
        _add_kv(args, template, "key_int", "--key-int")
        _add_kv(args, template, "key_float", "--key-float")
        _add_kv(args, template, "key_bool", "--key-bool")
        assert args == ["--key-int", "42", "--key-float", "3.14", "--key-bool", "True"]