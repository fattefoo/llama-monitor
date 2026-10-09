# -*- coding: utf-8 -*-
"""Single source of truth for llama-server template parameters.

Every parameter offered by the template wizard lives in :data:`PARAM_SPECS`
together with the llama-server flag it maps to, its llama-server default, and
an optional ``when`` predicate describing the related parameter values it
depends on.

Both sides consume this table:

* ``daemon._build_llama_args`` emits the flags (and skips parameters whose
  ``when`` predicate is false, so a stale template never passes a flag that
  contradicts another value).
* ``cli`` builds its prompts from the table, adapting it to the selected
  llama-server build via ``--help`` introspection.

Because the flag name lives here, adding a parameter only requires one entry.
"""

from typing import Callable, Optional


class ParamSpec:
    """One llama-server parameter: template key, CLI flag, prompt metadata."""

    __slots__ = ("key", "flag", "kind", "options", "default", "group",
                 "always_ask", "help", "off_flag", "when")

    def __init__(self, key: str, flag: str, kind: str = "int", default=None,
                 options=None, group: str = "Other", always_ask: bool = False,
                 help: str = "", off_flag: Optional[str] = None,
                 when: Optional[Callable[[dict], bool]] = None):
        self.key = key
        self.flag = flag
        self.kind = kind          # int | float | bool | str | select
        self.options = options or []   # list of (value, display)
        self.default = default    # llama-server default (None = unset)
        self.group = group
        self.always_ask = always_ask
        self.help = help
        self.off_flag = off_flag  # "--no-x" form for booleans, when supported
        self.when = when          # predicate over the template dict

    # Backwards-compatible alias used by the wizard.
    @property
    def label(self) -> str:
        return self.key

    def active(self, template: dict) -> bool:
        """True when this parameter is meaningful for the template's values."""
        return self.when(template) if self.when else True

    def clone(self, **overrides) -> "ParamSpec":
        """Copy of this spec with the given attributes replaced."""
        fields = {name: getattr(self, name) for name in self.__slots__}
        fields.update(overrides)
        spec = ParamSpec(fields["key"], fields["flag"])
        for name, value in fields.items():
            setattr(spec, name, value)
        return spec

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<ParamSpec {self.key} -> {self.flag}>"


# Short alias used by the table below for readability.
_ParamSpec = ParamSpec


# --------------------------------------------------------------------------
# Dependency helpers
# --------------------------------------------------------------------------

def _num(value, default=0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _int(value, default=0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _spec_types(template: dict) -> set:
    """Split --spec-type into a set of enabled spec methods."""
    raw = str(template.get("spec_type", "none") or "none")
    return {p.strip() for p in raw.split(",") if p.strip() and p.strip() != "none"}


def spec_enabled(t: dict) -> bool:
    """Any speculative decoding method enabled."""
    return bool(_spec_types(t))


def spec_draft_enabled(t: dict) -> bool:
    """A draft-model based spec method (needs --spec-draft-model)."""
    return bool({s for s in _spec_types(t) if s.startswith("draft-")})


def spec_ngram_enabled(t: dict) -> bool:
    """An ngram based spec method (no draft model needed)."""
    return bool({s for s in _spec_types(t) if s.startswith("ngram-")})


def spec_ngram_types(t: dict) -> set:
    return {s for s in _spec_types(t) if s.startswith("ngram-")}


def spec_ngram_simple(t: dict) -> bool:
    return bool(spec_ngram_types(t) & {"ngram-simple"})


def spec_ngram_map_k(t: dict) -> bool:
    return bool(spec_ngram_types(t) & {"ngram-map-k"})


def spec_ngram_map_k4v(t: dict) -> bool:
    return bool(spec_ngram_types(t) & {"ngram-map-k4v"})


def spec_ngram_mod(t: dict) -> bool:
    return bool(spec_ngram_types(t) & {"ngram-mod"})


def mirostat_enabled(t: dict) -> bool:
    return _int(t.get("mirostat", 0)) > 0


def mirostat_v1_enabled(t: dict) -> bool:
    """Mirostat 1.0 (the version that uses a target entropy)."""
    return _int(t.get("mirostat", 0)) == 1


# --------------------------------------------------------------------------
# Option helpers
# --------------------------------------------------------------------------

def ctx_options():
    """Context sizes: n*1024 tokens for n in 8..256 in steps of 8, plus 0."""
    return [(0, "0 (from model)")] + [
        (n * 1024, f"{n}k ({n * 1024})") for n in range(8, 257, 8)]


CACHE_TYPES = [
    "f32", "f16", "bf16", "q8_0", "q4_0", "q4_1", "iq4_nl", "q5_0", "q5_1",
    "turbo2", "turbo3", "turbo4",
]

_ON_OFF = [("on", "on"), ("off", "off"), ("auto", "auto")]


# --------------------------------------------------------------------------
# The table
# --------------------------------------------------------------------------

PARAM_SPECS = [
    # --- Server: network ---------------------------------------------------
    _ParamSpec("port", "--port", kind="int", default=8080, group="Server",
               always_ask=True, help="TCP port llama-server listens on"),
    _ParamSpec("bind", "--host", kind="str", default="127.0.0.1",
               group="Server", always_ask=True, help="0.0.0.0 for LAN access"),
    _ParamSpec("api_prefix", "--api-prefix", kind="str", default="",
               group="Server", help="path prefix for all endpoints"),
    _ParamSpec("path", "--path", kind="str", default="",
               group="Server", help="serve static files from this dir"),
    _ParamSpec("timeout", "--timeout", kind="int", default=3600,
               group="Server", help="read/write timeout (seconds)"),
    _ParamSpec("sse_ping_interval", "--sse-ping-interval", kind="int",
               default=30, group="Server", help="-1 disables SSE pings"),
    _ParamSpec("alias", "--alias", kind="str", default="", group="Server",
               help="model name aliases, comma-separated"),
    _ParamSpec("tags", "--tags", kind="str", default="", group="Server",
               help="model tags, comma-separated"),

    # --- Server: access control -------------------------------------------
    _ParamSpec("api_key", "--api-key", kind="str", default="",
               group="Access control", help="API key, comma-separated"),
    _ParamSpec("api_key_file", "--api-key-file", kind="str", default="",
               group="Access control", help="file with API keys"),
    _ParamSpec("ssl_key_file", "--ssl-key-file", kind="str", default="",
               group="Access control", help="PEM private key"),
    _ParamSpec("ssl_cert_file", "--ssl-cert-file", kind="str", default="",
               group="Access control", help="PEM certificate"),
    _ParamSpec("cors_origins", "--cors-origins", kind="str", default="*",
               group="Access control", help="comma-separated allowed origins"),
    _ParamSpec("cors_methods", "--cors-methods", kind="str", default="",
               group="Access control",
               help='e.g. "GET, POST, DELETE, OPTIONS" (empty = default)'),
    _ParamSpec("cors_headers", "--cors-headers", kind="str", default="*",
               group="Access control", help="comma-separated allowed headers"),
    _ParamSpec("cors_credentials", "--cors-credentials", kind="bool",
               default=True, off_flag="--no-cors-credentials",
               group="Access control"),
    _ParamSpec("reuse_port", "--reuse-port", kind="bool", default=False,
               off_flag="--no-reuse-port", group="Access control"),

    # --- Server: endpoints and features ------------------------------------
    _ParamSpec("webui", "--webui", kind="bool", default=True,
               off_flag="--no-webui", group="Server features"),
    _ParamSpec("metrics", "--metrics", kind="bool", default=False,
               group="Server features"),
    _ParamSpec("props", "--props", kind="bool", default=False,
               off_flag="--no-props", group="Server features",
               help="allow changing global props via POST /props"),
    _ParamSpec("slots", "--slots", kind="bool", default=True,
               off_flag="--no-slots", group="Server features",
               help="expose /slots monitoring endpoint"),
    _ParamSpec("agent", "--agent", kind="bool", default=False,
               off_flag="--no-agent", group="Server features",
               help="CORS proxy + all built-in tools (untrusted hosts: no)"),
    _ParamSpec("tools", "--tools", kind="str", default="", group="Server features",
               help="built-in agent tools, comma-separated (\"all\" for all)"),
    _ParamSpec("mcp_servers_config", "--mcp-servers-config", kind="str",
               default="", group="Server features", help="MCP servers JSON file"),
    _ParamSpec("mcp_servers_json", "--mcp-servers-json", kind="str", default="",
               group="Server features", help="inline MCP servers JSON"),
    _ParamSpec("ui_config", "--ui-config", kind="str", default="",
               group="Server features", help="default Web UI settings JSON"),
    _ParamSpec("embedding", "--embedding", kind="bool", default=False,
               group="Server features", help="embedding models only"),
    _ParamSpec("rerank", "--rerank", kind="bool", default=False,
               group="Server features", help="enable reranking endpoint"),
    _ParamSpec("pooling", "--pooling", kind="select", default="",
               options=[("", "model default (unset)"), ("none", "none"),
                        ("mean", "mean"), ("cls", "cls"), ("last", "last"),
                        ("rank", "rank")],
               group="Server features",
               when=lambda t: bool(t.get("embedding")),
               help="required for embeddings"),
    _ParamSpec("embd_normalize", "--embd-normalize", kind="int", default=2,
               group="Server features", when=lambda t: bool(t.get("embedding")),
               help="-1=none 0=max-abs 1=taxicab 2=euclidean >2=p-norm"),
    _ParamSpec("sleep_idle_seconds", "--sleep-idle-seconds", kind="int",
               default=-1, group="Server features",
               help="idle seconds before sleeping, -1 = disabled"),
    _ParamSpec("special", "--special", kind="bool", default=False,
               group="Server features", help="output special tokens"),
    _ParamSpec("reverse_prompt", "--reverse-prompt", kind="str", default="",
               group="Server features", help="halt generation at this prompt"),

    # --- Server: chat / templates -----------------------------------------
    _ParamSpec("jinja", "--jinja", kind="bool", default=True,
               off_flag="--no-jinja", group="Chat",
               help="use jinja chat template engine"),
    # Legacy key kept so old templates keep working; renamed by the wizard.
    _ParamSpec("chat_format", "--chat-format", kind="str", default="",
               group="Chat"),
    _ParamSpec("chat_template", "--chat-template", kind="str", default="",
               group="Chat", help="builtin template name (see --help list)"),
    _ParamSpec("chat_template_file", "--chat-template-file", kind="str",
               default="", group="Chat", help="path to a jinja template file"),
    _ParamSpec("chat_template_kwargs", "--chat-template-kwargs", kind="str",
               default="", group="Chat", help='JSON object string, e.g. \'{"enable_thinking":false}\''),
    _ParamSpec("reasoning", "--reasoning", kind="select", default="auto",
               options=_ON_OFF, group="Chat",
               help="use reasoning/thinking in the chat"),
    _ParamSpec("reasoning_format", "--reasoning-format", kind="select",
               default="auto",
               options=[("auto", "auto"), ("none", "none"),
                        ("deepseek", "deepseek"),
                        ("deepseek-legacy", "deepseek-legacy")],
               group="Chat"),
    _ParamSpec("reasoning_effort", "--reasoning-effort", kind="str",
               default="default", group="Chat",
               help="'default', minimal, low, medium, high, xhigh, max"),
    _ParamSpec("reasoning_budget", "--reasoning-budget", kind="int", default=-1,
               group="Chat",
               when=lambda t: t.get("reasoning", "auto") not in ("off", None, False),
               help="-1 unrestricted, 0 immediate end, N token budget"),
    _ParamSpec("reasoning_budget_message", "--reasoning-budget-message",
               kind="str", default="", group="Chat",
               when=lambda t: t.get("reasoning", "auto") not in ("off", None, False)),
    _ParamSpec("reasoning_preserve", "--reasoning-preserve", kind="bool",
               default=True, off_flag="--no-reasoning-preserve", group="Chat"),
    _ParamSpec("skip_chat_parsing", "--skip-chat-parsing", kind="bool",
               default=False, off_flag="--no-skip-chat-parsing", group="Chat",
               help="force a pure content parser"),
    _ParamSpec("prefill_assistant", "--prefill-assistant", kind="bool",
               default=True, off_flag="--no-prefill-assistant", group="Chat"),
    _ParamSpec("grammar", "--grammar", kind="str", default="", group="Chat",
               when=lambda t: not t.get("json_schema"),
               help="BNF-like grammar constraining generations"),
    _ParamSpec("grammar_file", "--grammar-file", kind="str", default="",
               group="Chat", when=lambda t: not t.get("json_schema")),
    _ParamSpec("json_schema", "--json-schema", kind="str", default="",
               group="Chat", help='JSON schema, e.g. "{}"'),
    _ParamSpec("json_schema_file", "--json-schema-file", kind="str", default="",
               group="Chat"),

    # --- Context / cache ---------------------------------------------------
    _ParamSpec("ctx_size", "--ctx-size", kind="select", default=0,
               options=ctx_options(), group="Context & cache",
               always_ask=True, help="in tokens (0 = from model)"),
    _ParamSpec("parallel", "--parallel", kind="int", default=-1,
               group="Context & cache", always_ask=True, help="-1 = auto slots"),
    _ParamSpec("batch_size", "--batch-size", kind="int", default=2048,
               group="Context & cache", help="logical max batch size"),
    _ParamSpec("ubatch_size", "--ubatch-size", kind="int", default=512,
               group="Context & cache", help="physical max batch size"),
    _ParamSpec("predict", "--predict", kind="int", default=-1,
               group="Context & cache", help="tokens to predict, -1 = unlimited"),
    _ParamSpec("keep", "--keep", kind="int", default=0, group="Context & cache",
               help="tokens kept from the initial prompt"),
    _ParamSpec("swa_full", "--swa-full", kind="bool", default=False,
               group="Context & cache", help="full-size SWA cache"),
    _ParamSpec("cont_batching", "--cont-batching", kind="bool", default=True,
               off_flag="--no-cont-batching", group="Context & cache",
               help="continuous/dynamic batching"),
    _ParamSpec("cache_prompt", "--cache-prompt", kind="bool", default=True,
               off_flag="--no-cache-prompt", group="Context & cache"),
    _ParamSpec("cache_reuse", "--cache-reuse", kind="int", default=0,
               group="Context & cache",
               when=lambda t: bool(t.get("cache_prompt", True)),
               help="min chunk size for KV-shift reuse"),
    _ParamSpec("cache_ram", "--cache-ram", kind="int", default=8192,
               group="Context & cache",
               help="prompt cache size in MiB, -1 = no limit, 0 = off"),
    _ParamSpec("cache_idle_slots", "--cache-idle-slots", kind="bool",
               default=True, off_flag="--no-cache-idle-slots",
               group="Context & cache",
               when=lambda t: _int(t.get("cache_ram", 0)) > 0,
               help="save idle slots to the prompt cache"),
    _ParamSpec("kv_unified", "--kv-unified", kind="bool", default=True,
               off_flag="--no-kv-unified", group="Context & cache",
               help="single KV buffer shared across sequences"),
    _ParamSpec("kv_unified_per_slot", "--kv-unified-per-slot", kind="int",
               default=0, group="Context & cache",
               help="context limit per slot (0 = unchanged)"),
    _ParamSpec("ctx_checkpoints", "--ctx-checkpoints", kind="int", default=32,
               group="Context & cache", help="max context checkpoints per slot"),
    _ParamSpec("checkpoint_min_step", "--checkpoint-min-step", kind="int",
               default=8192, group="Context & cache",
               help="min token spacing between checkpoints, 0 = no minimum"),
    _ParamSpec("slot_prompt_similarity", "--slot-prompt-similarity",
               kind="float", default=0.10, group="Context & cache",
               help="slot reuse threshold, 0 = disabled"),
    _ParamSpec("slot_save_path", "--slot-save-path", kind="str", default="",
               group="Context & cache", help="where to save slot KV cache"),
    _ParamSpec("context_shift", "--context-shift", kind="bool", default=False,
               off_flag="--no-context-shift", group="Context & cache"),
    _ParamSpec("cache_type_k", "--cache-type-k", kind="select", default="f16",
               options=[(t, t) for t in CACHE_TYPES], group="KV cache"),
    _ParamSpec("cache_type_v", "--cache-type-v", kind="select", default="f16",
               options=[(t, t) for t in CACHE_TYPES], group="KV cache"),
    _ParamSpec("lookup_cache_static", "--lookup-cache-static", kind="str",
               default="", group="KV cache",
               when=lambda t: "ngram-cache" in _spec_types(t)),
    _ParamSpec("lookup_cache_dynamic", "--lookup-cache-dynamic", kind="str",
               default="", group="KV cache",
               when=lambda t: "ngram-cache" in _spec_types(t)),

    # --- CPU / threads -----------------------------------------------------
    _ParamSpec("threads", "--threads", kind="int", default=-1,
               group="CPU & threads", help="-1 = all cores"),
    _ParamSpec("threads_batch", "--threads-batch", kind="int", default=-1,
               group="CPU & threads", help="-1 = same as --threads"),
    _ParamSpec("threads_http", "--threads-http", kind="int", default=-1,
               group="CPU & threads", help="-1 = same as --threads"),
    _ParamSpec("cpu_mask", "--cpu-mask", kind="str", default="",
               group="CPU & threads", help="CPU affinity mask (hex)"),
    _ParamSpec("cpu_range", "--cpu-range", kind="str", default="",
               group="CPU & threads", help="CPU affinity range, e.g. 0-7"),
    _ParamSpec("cpu_strict", "--cpu-strict", kind="bool", default=False,
               group="CPU & threads", help="strict CPU placement"),
    _ParamSpec("cpu_mask_batch", "--cpu-mask-batch", kind="str", default="",
               group="CPU & threads"),
    _ParamSpec("cpu_range_batch", "--cpu-range-batch", kind="str", default="",
               group="CPU & threads"),
    _ParamSpec("cpu_strict_batch", "--cpu-strict-batch", kind="bool",
               default=False, group="CPU & threads"),
    _ParamSpec("prio", "--prio", kind="int", default=0, group="CPU & threads",
               help="-1=low 0=normal 1=medium 2=high 3=realtime"),
    _ParamSpec("poll", "--poll", kind="int", default=50, group="CPU & threads",
               help="0 = no polling"),
    _ParamSpec("prio_batch", "--prio-batch", kind="int", default=0,
               group="CPU & threads"),
    _ParamSpec("poll_batch", "--poll-batch", kind="int", default=50,
               group="CPU & threads"),
    _ParamSpec("numa", "--numa", kind="select", default="",
               options=[("", "unset"), ("distribute", "distribute"),
                        ("isolate", "isolate"), ("numactl", "numactl")],
               group="CPU & threads"),

    # --- GPU / offload -----------------------------------------------------
    _ParamSpec("n_gpu_layers", "--n-gpu-layers", kind="str", default="auto",
               group="GPU & offload", always_ask=True,
               help="-1 = all, 0 = CPU only, 'auto', 'all'"),
    _ParamSpec("split_mode", "--split-mode", kind="select", default="layer",
               options=[("none", "none (single GPU)"), ("layer", "layer"),
                        ("row", "row"), ("tensor", "tensor (experimental)")],
               group="GPU & offload"),
    _ParamSpec("tensor_split", "--tensor-split", kind="str", default="",
               group="GPU & offload", when=lambda t: t.get("split_mode") != "none",
               help='fractions per GPU, e.g. "3,1"'),
    _ParamSpec("main_gpu", "--main-gpu", kind="int", default=0,
               group="GPU & offload",
               help="GPU for the model (split-mode none) or KV (row)"),
    _ParamSpec("device", "--device", kind="str", default="",
               group="GPU & offload",
               help='offload devices, e.g. "CUDA0,CUDA1"; "none" = CPU'),
    _ParamSpec("override_tensor", "--override-tensor", kind="str", default="",
               group="GPU & offload", help="pattern=buffer-type[,...]"),
    _ParamSpec("fit", "--fit", kind="select", default="on", options=_ON_OFF,
               group="GPU & offload",
               help="auto-adjust unset args to fit device memory"),
    _ParamSpec("fit_target", "--fit-target", kind="str", default="",
               group="GPU & offload",
               help="per-device margin in MiB for --fit (default 1024)"),
    _ParamSpec("fit_ctx", "--fit-ctx", kind="int", default=4096,
               group="GPU & offload",
               help="minimum ctx size --fit may set"),
    _ParamSpec("n_cpu_moe", "--n-cpu-moe", kind="int", default=0,
               group="GPU & offload", help="MoE layers of the first N kept on CPU"),
    _ParamSpec("n_cpu_ffn", "--n-cpu-ffn", kind="int", default=0,
               group="GPU & offload", help="dense FFN layers kept on CPU"),
    _ParamSpec("cpu_moe", "--cpu-moe", kind="bool", default=False,
               off_flag="--no-cpu-moe", group="GPU & offload",
               help="keep all MoE weights on the CPU"),
    _ParamSpec("kv_offload", "--kv-offload", kind="bool", default=True,
               off_flag="--no-kv-offload", group="GPU & offload"),
    _ParamSpec("repack", "--repack", kind="bool", default=True,
               off_flag="--no-repack", group="GPU & offload"),
    _ParamSpec("op_offload", "--op-offload", kind="bool", default=True,
               off_flag="--no-op-offload", group="GPU & offload"),
    _ParamSpec("no_host", "--no-host", kind="bool", default=False,
               group="GPU & offload", help="bypass host buffer"),
    _ParamSpec("flash_attn", "--flash-attn", kind="select", default="auto",
               options=_ON_OFF, group="GPU & offload"),
    _ParamSpec("load_mode", "--load-mode", kind="select", default="auto",
               options=[("", "server default (unset)"), ("auto", "auto"),
                        ("none", "none"), ("mmap", "mmap"), ("mlock", "mlock"),
                        ("mmap+mlock", "mmap+mlock"), ("dio", "dio")],
               group="GPU & offload"),
    _ParamSpec("lazy_mode", "--lazy-mode", kind="select", default="auto",
               options=[("", "server default (unset)"), ("on", "on"),
                        ("auto", "auto"), ("off", "off")],
               group="GPU & offload"),
    _ParamSpec("check_tensors", "--check-tensors", kind="bool", default=False,
               group="GPU & offload", help="validate model tensor data"),
    _ParamSpec("rope_scaling", "--rope-scaling", kind="select", default="",
               options=[("", "model default (unset)"), ("none", "none"),
                        ("linear", "linear"), ("yarn", "yarn")],
               group="GPU & offload"),
    _ParamSpec("rope_scale", "--rope-scale", kind="float", default=1.0,
               group="GPU & offload", help="RoPE context scaling factor"),
    _ParamSpec("rope_freq_base", "--rope-freq-base", kind="float", default=0.0,
               group="GPU & offload", help="0 = from model"),
    _ParamSpec("rope_freq_scale", "--rope-freq-scale", kind="float", default=1.0,
               group="GPU & offload", help="scaling by a factor of 1/N"),
    _ParamSpec("yarn_orig_ctx", "--yarn-orig-ctx", kind="int", default=0,
               group="GPU & offload", when=lambda t: t.get("rope_scaling") == "yarn",
               help="original context size, 0 = model training context"),
    _ParamSpec("yarn_ext_factor", "--yarn-ext-factor", kind="float", default=-1.0,
               group="GPU & offload", when=lambda t: t.get("rope_scaling") == "yarn"),
    _ParamSpec("yarn_attn_factor", "--yarn-attn-factor", kind="float",
               default=-1.0, group="GPU & offload",
               when=lambda t: t.get("rope_scaling") == "yarn"),
    _ParamSpec("yarn_beta_slow", "--yarn-beta-slow", kind="float", default=-1.0,
               group="GPU & offload", when=lambda t: t.get("rope_scaling") == "yarn"),
    _ParamSpec("yarn_beta_fast", "--yarn-beta-fast", kind="float", default=-1.0,
               group="GPU & offload", when=lambda t: t.get("rope_scaling") == "yarn"),

    # --- Multimodal --------------------------------------------------------
    _ParamSpec("mmproj", "--mmproj", kind="str", default="",
               group="Multimodal", help="path to a multimodal projector"),
    _ParamSpec("mmproj_url", "--mmproj-url", kind="str", default="",
               group="Multimodal", help="projector URL"),
    _ParamSpec("mmproj_auto", "--mmproj-auto", kind="bool", default=True,
               off_flag="--no-mmproj-auto", group="Multimodal",
               help="auto-use a projector if available"),
    _ParamSpec("mmproj_offload", "--mmproj-offload", kind="bool", default=True,
               off_flag="--no-mmproj-offload", group="Multimodal"),
    _ParamSpec("mmproj_device", "--mmproj-device", kind="str", default="",
               group="Multimodal", help="device for the projector"),
    _ParamSpec("image_min_tokens", "--image-min-tokens", kind="int", default=0,
               group="Multimodal", help="0 = read from model"),
    _ParamSpec("image_max_tokens", "--image-max-tokens", kind="int", default=0,
               group="Multimodal", help="0 = read from model"),
    _ParamSpec("mtmd_batch_max_tokens", "--mtmd-batch-max-tokens", kind="int",
               default=1024, group="Multimodal"),
    _ParamSpec("video_fps", "--video-fps", kind="float", default=4.0,
               group="Multimodal"),
    _ParamSpec("video_timestamp_interval", "--video-timestamp-interval",
               kind="int", default=5000, group="Multimodal",
               help="milliseconds between text timestamps"),
    _ParamSpec("video_ffmpeg_dir", "--video-ffmpeg-dir", kind="str", default="",
               group="Multimodal", help="dir containing ffmpeg/ffprobe"),

    # --- LoRA / control vectors -------------------------------------------
    _ParamSpec("lora", "--lora", kind="str", default="", group="LoRA",
               help="path to a LoRA adapter"),
    _ParamSpec("lora_scaled", "--lora-scaled", kind="str", default="",
               group="LoRA", help="FNAME:SCALE[,...]"),
    _ParamSpec("lora_init_without_apply", "--lora-init-without-apply",
               kind="bool", default=False,
               off_flag="--no-lora-init-without-apply", group="LoRA"),
    _ParamSpec("control_vector", "--control-vector", kind="str", default="",
               group="LoRA", help="comma-separated control vector files"),
    _ParamSpec("control_vector_scaled", "--control-vector-scaled", kind="str",
               default="", group="LoRA", help="FNAME:SCALE[,...]"),
    _ParamSpec("control_vector_layer_range", "--control-vector-layer-range",
               kind="str", default="", group="LoRA", help="START END"),

    # --- Sampling ----------------------------------------------------------
    _ParamSpec("mirostat", "--mirostat", kind="int", default=0,
               group="Sampling",
               help="0 = off, 1 = Mirostat, 2 = Mirostat 2.0 (disables top-k/p)"),
    _ParamSpec("temperature", "--temp", kind="float", default=0.80,
               group="Sampling"),
    _ParamSpec("dynatemp_range", "--dynatemp-range", kind="float", default=0.0,
               group="Sampling", help="0 = disabled"),
    _ParamSpec("dynatemp_exp", "--dynatemp-exp", kind="float", default=1.0,
               group="Sampling",
               when=lambda t: _num(t.get("dynatemp_range")) > 0),
    _ParamSpec("top_k", "--top-k", kind="int", default=40, group="Sampling",
               when=lambda t: not mirostat_enabled(t), help="0 = disabled"),
    _ParamSpec("top_p", "--top-p", kind="float", default=0.95, group="Sampling",
               when=lambda t: not mirostat_enabled(t), help="1.0 = disabled"),
    _ParamSpec("top_nsigma", "--top-n-sigma", kind="float", default=-1.0,
               group="Sampling",
               when=lambda t: not mirostat_enabled(t), help="-1 = disabled"),
    _ParamSpec("typical_p", "--typical-p", kind="float", default=1.0,
               group="Sampling",
               when=lambda t: not mirostat_enabled(t), help="1.0 = disabled"),
    _ParamSpec("min_p", "--min-p", kind="float", default=0.05, group="Sampling",
               help="0.0 = disabled"),
    _ParamSpec("xtc_probability", "--xtc-probability", kind="float", default=0.0,
               group="Sampling", help="0 = disabled"),
    _ParamSpec("xtc_threshold", "--xtc-threshold", kind="float", default=0.10,
               group="Sampling",
               when=lambda t: _num(t.get("xtc_probability")) > 0,
               help="1.0 = disabled"),
    _ParamSpec("adaptive_target", "--adaptive-target", kind="float", default=-1.0,
               group="Sampling", help="-1 = disabled"),
    _ParamSpec("adaptive_decay", "--adaptive-decay", kind="float", default=0.90,
               group="Sampling",
               when=lambda t: _num(t.get("adaptive_target")) >= 0),
    _ParamSpec("repeat_last_n", "--repeat-last-n", kind="int", default=64,
               group="Sampling", help="0 = disabled"),
    _ParamSpec("repeat_penalty", "--repeat-penalty", kind="float", default=1.0,
               group="Sampling", help="1.0 = disabled"),
    _ParamSpec("presence_penalty", "--presence-penalty", kind="float",
               default=0.0, group="Sampling", help="0.0 = disabled"),
    _ParamSpec("frequency_penalty", "--frequency-penalty", kind="float",
               default=0.0, group="Sampling", help="0.0 = disabled"),
    _ParamSpec("dry_multiplier", "--dry-multiplier", kind="float", default=0.0,
               group="DRY sampling", help="0 = disabled"),
    _ParamSpec("dry_base", "--dry-base", kind="float", default=1.75,
               group="DRY sampling",
               when=lambda t: _num(t.get("dry_multiplier")) > 0),
    _ParamSpec("dry_allowed_length", "--dry-allowed-length", kind="int",
               default=2, group="DRY sampling",
               when=lambda t: _num(t.get("dry_multiplier")) > 0),
    _ParamSpec("dry_penalty_last_n", "--dry-penalty-last-n", kind="int",
               default=64, group="DRY sampling",
               when=lambda t: _num(t.get("dry_multiplier")) > 0,
               help="0 = disabled"),
    _ParamSpec("dry_sequence_breaker", "--dry-sequence-breaker", kind="str",
               default="", group="DRY sampling",
               when=lambda t: _num(t.get("dry_multiplier")) > 0,
               help='replaces the default breakers; "none" clears them'),
    _ParamSpec("mirostat_lr", "--mirostat-lr", kind="float", default=0.10,
               group="Mirostat", when=mirostat_enabled,
               help="learning rate eta (both versions)"),
    _ParamSpec("mirostat_ent", "--mirostat-ent", kind="float", default=5.00,
               group="Mirostat", when=mirostat_v1_enabled,
               help="target entropy tau (Mirostat 1 only)"),
    _ParamSpec("seed", "--seed", kind="int", default=-1, group="Sampling",
               help="-1 = random seed"),
    _ParamSpec("logit_bias", "--logit-bias", kind="str", default="",
               group="Sampling", help='e.g. "15043-1" or "15043+1"'),
    _ParamSpec("ignore_eos", "--ignore-eos", kind="bool", default=False,
               group="Sampling", help="continue past end-of-stream"),
    _ParamSpec("samplers", "--samplers", kind="str", default="",
               group="Sampling", help="custom sampler order, ';'-separated"),
    _ParamSpec("sampler_seq", "--sampler-seq", kind="str", default="",
               group="Sampling", help="simplified sampler sequence"),
    _ParamSpec("backend_sampling", "--backend-sampling", kind="bool",
               default=False, off_flag="--no-backend-sampling",
               group="Sampling", help="experimental backend sampling"),

    # --- Speculative decoding ---------------------------------------------
    _ParamSpec("spec_type", "--spec-type", kind="select", default="none",
               options=[("none", "none (disabled)"),
                        ("draft-simple", "draft-simple"),
                        ("draft-eagle3", "draft-eagle3"),
                        ("draft-mtp", "draft-mtp"),
                        ("draft-dflash", "draft-dflash"),
                        ("draft-dspark", "draft-dspark"),
                        ("ngram-simple", "ngram-simple"),
                        ("ngram-map-k", "ngram-map-k"),
                        ("ngram-map-k4v", "ngram-map-k4v"),
                        ("ngram-mod", "ngram-mod"),
                        ("ngram-cache", "ngram-cache")],
               group="Speculative decoding",
               help="comma-separated list of methods"),
    _ParamSpec("spec_draft_model", "--spec-draft-model", kind="str",
               default="", group="Speculative decoding",
               when=spec_draft_enabled, help="path to the draft model"),
    _ParamSpec("spec_draft_n_max", "--spec-draft-n-max", kind="int", default=3,
               group="Speculative decoding", when=spec_draft_enabled,
               help="number of tokens to draft"),
    _ParamSpec("spec_draft_n_min", "--spec-draft-n-min", kind="int", default=0,
               group="Speculative decoding", when=spec_draft_enabled,
               help="minimum number of draft tokens to use"),
    _ParamSpec("spec_draft_p_split", "--spec-draft-p-split", kind="float",
               default=0.10, group="Speculative decoding", when=spec_draft_enabled,
               help="speculative decoding split probability"),
    _ParamSpec("spec_draft_p_min", "--spec-draft-p-min", kind="float",
               default=0.00, group="Speculative decoding", when=spec_draft_enabled,
               help="minimum probability (greedy)"),
    _ParamSpec("spec_draft_backend_sampling", "--spec-draft-backend-sampling",
               kind="bool", default=True, off_flag="--no-spec-draft-backend-sampling",
               group="Speculative decoding", when=spec_draft_enabled),
    _ParamSpec("spec_draft_ngl", "--n-gpu-layers-draft", kind="str",
               default="", group="Speculative decoding", when=spec_draft_enabled,
               help="draft model layers in VRAM ('auto', 'all', N)"),
    _ParamSpec("spec_draft_device", "--device-draft", kind="str", default="",
               group="Speculative decoding", when=spec_draft_enabled),
    _ParamSpec("spec_draft_threads", "--threads-draft", kind="int", default=0,
               group="Speculative decoding", when=spec_draft_enabled,
               help="0 = same as --threads"),
    _ParamSpec("spec_draft_threads_batch", "--threads-batch-draft", kind="int",
               default=0, group="Speculative decoding", when=spec_draft_enabled),
    _ParamSpec("spec_draft_cpu_mask", "--cpu-mask-draft", kind="str", default="",
               group="Speculative decoding", when=spec_draft_enabled),
    _ParamSpec("spec_draft_cpu_range", "--cpu-range-draft", kind="str", default="",
               group="Speculative decoding", when=spec_draft_enabled),
    _ParamSpec("spec_draft_cpu_strict", "--cpu-strict-draft", kind="bool",
               default=False, off_flag="--no-cpu-strict-draft",
               group="Speculative decoding", when=spec_draft_enabled),
    _ParamSpec("spec_draft_prio", "--prio-draft", kind="int", default=0,
               group="Speculative decoding", when=spec_draft_enabled),
    _ParamSpec("spec_draft_poll", "--poll-draft", kind="int", default=0,
               group="Speculative decoding", when=spec_draft_enabled),
    _ParamSpec("spec_draft_cpu_moe", "--cpu-moe-draft", kind="bool",
               default=False, off_flag="--no-cpu-moe-draft",
               group="Speculative decoding", when=spec_draft_enabled),
    _ParamSpec("spec_draft_n_cpu_moe", "--n-cpu-moe-draft", kind="int", default=0,
               group="Speculative decoding", when=spec_draft_enabled),
    _ParamSpec("spec_draft_override_tensor", "--override-tensor-draft",
               kind="str", default="", group="Speculative decoding",
               when=spec_draft_enabled),
    _ParamSpec("spec_draft_cache_type_k", "--cache-type-k-draft", kind="select",
               default="", options=[("", "server default (unset)")]
               + [(t, t) for t in CACHE_TYPES],
               group="Speculative decoding", when=spec_draft_enabled),
    _ParamSpec("spec_draft_cache_type_v", "--cache-type-v-draft", kind="select",
               default="", options=[("", "server default (unset)")]
               + [(t, t) for t in CACHE_TYPES],
               group="Speculative decoding", when=spec_draft_enabled),
    _ParamSpec("spec_ngram_simple_size_n", "--spec-ngram-simple-size-n",
               kind="int", default=12, group="Speculative decoding",
               when=spec_ngram_simple, help="lookup n-gram length"),
    _ParamSpec("spec_ngram_simple_size_m", "--spec-ngram-simple-size-m",
               kind="int", default=48, group="Speculative decoding",
               when=spec_ngram_simple, help="draft m-gram length"),
    _ParamSpec("spec_ngram_simple_min_hits", "--spec-ngram-simple-min-hits",
               kind="int", default=1, group="Speculative decoding",
               when=spec_ngram_simple),
    _ParamSpec("spec_ngram_map_k_size_n", "--spec-ngram-map-k-size-n",
               kind="int", default=12, group="Speculative decoding",
               when=spec_ngram_map_k),
    _ParamSpec("spec_ngram_map_k_size_m", "--spec-ngram-map-k-size-m",
               kind="int", default=48, group="Speculative decoding",
               when=spec_ngram_map_k),
    _ParamSpec("spec_ngram_map_k_min_hits", "--spec-ngram-map-k-min-hits",
               kind="int", default=1, group="Speculative decoding",
               when=spec_ngram_map_k),
    _ParamSpec("spec_ngram_map_k4v_size_n", "--spec-ngram-map-k4v-size-n",
               kind="int", default=12, group="Speculative decoding",
               when=spec_ngram_map_k4v),
    _ParamSpec("spec_ngram_map_k4v_size_m", "--spec-ngram-map-k4v-size-m",
               kind="int", default=48, group="Speculative decoding",
               when=spec_ngram_map_k4v),
    _ParamSpec("spec_ngram_map_k4v_min_hits", "--spec-ngram-map-k4v-min-hits",
               kind="int", default=1, group="Speculative decoding",
               when=spec_ngram_map_k4v),
    _ParamSpec("spec_ngram_mod_n_min", "--spec-ngram-mod-n-min", kind="int",
               default=48, group="Speculative decoding", when=spec_ngram_mod,
               help="minimum ngram tokens to use"),
    _ParamSpec("spec_ngram_mod_n_max", "--spec-ngram-mod-n-max", kind="int",
               default=64, group="Speculative decoding", when=spec_ngram_mod),
    _ParamSpec("spec_ngram_mod_n_match", "--spec-ngram-mod-n-match", kind="int",
               default=24, group="Speculative decoding", when=spec_ngram_mod,
               help="ngram-mod lookup length"),

    # --- Logging / misc ----------------------------------------------------
    _ParamSpec("verbosity", "--verbosity", kind="int", default=3,
               group="Logging", help="0=generic 1=error 2=warn 3=info 4=trace 5=debug"),
    _ParamSpec("log_file", "--log-file", kind="str", default="",
               group="Logging", help="also write logs to this file"),
    _ParamSpec("log_disable", "--log-disable", kind="bool", default=False,
               group="Logging"),
    _ParamSpec("log_prefix", "--log-prefix", kind="bool", default=True,
               off_flag="--no-log-prefix", group="Logging"),
    _ParamSpec("log_timestamps", "--log-timestamps", kind="bool", default=True,
               off_flag="--no-log-timestamps", group="Logging"),
    _ParamSpec("log_colors", "--log-colors", kind="select", default="auto",
               options=_ON_OFF, group="Logging"),
    _ParamSpec("log_verbose", "--log-verbose", kind="bool", default=False,
               off_flag="--no-log-verbose", group="Logging",
               help="log everything"),
    _ParamSpec("log_prompts_dir", "--log-prompts-dir", kind="str", default="",
               group="Logging", help="debugging only"),
    _ParamSpec("offline", "--offline", kind="bool", default=False,
               group="Logging", help="prevent network access"),
    _ParamSpec("override_kv", "--override-kv", kind="str", default="",
               group="Logging", help="KEY=TYPE:VALUE model metadata overrides"),
]

# Parameters the wizard always offers, even for a fresh template. They are the
# knobs that materially change how llama-server runs or serves.
CORE_KEYS = {
    "port", "bind",
    "ctx_size", "parallel", "n_gpu_layers", "split_mode", "tensor_split",
    "n_cpu_moe", "flash_attn", "load_mode", "cache_type_k", "cache_type_v",
    "temperature", "top_p", "top_k", "min_p", "repeat_last_n", "seed",
    "spec_type", "jinja", "metrics",
}

PARAM_FLAGS = {spec.key: spec.flag for spec in PARAM_SPECS}

SPEC_BY_KEY = {spec.key: spec for spec in PARAM_SPECS}

# Group order as offered by the wizard.
GROUPS = []
for _spec in PARAM_SPECS:
    if _spec.group not in GROUPS:
        GROUPS.append(_spec.group)


class _Probe(dict):
    """A template dict that records which keys a predicate reads."""

    def __init__(self, base: dict):
        super().__init__(base)
        self.reads: set = set()

    def get(self, key, default=None):
        self.reads.add(key)
        return super().get(key, default)

    def __getitem__(self, key):
        self.reads.add(key)
        return super().__getitem__(key)

    def __contains__(self, key):
        self.reads.add(key)
        return super().__contains__(key)


def dependencies_of(spec: ParamSpec, template: dict) -> list:
    """Template keys whose values gate this spec.

    Derived by running the ``when`` predicate against a dict that records the
    keys it reads, so a predicate written as ``t.get("spec_type")`` reports
    ``spec_type`` without any extra bookkeeping.
    """
    if not spec.when:
        return []
    probe = _Probe(template)
    spec.when(probe)
    return sorted(probe.reads)


def candidate_values(template: dict, key: str) -> list:
    """Values worth trying for ``key`` when looking for a dependency fix."""
    spec = SPEC_BY_KEY.get(key)
    values: list = []
    if spec:
        if spec.kind == "select":
            values = [v for v, _ in spec.options if v != ""]
        elif spec.kind == "bool":
            values = [True, False]
        elif spec.kind in ("int", "float"):
            values = [0, 1, 2, 3, -1]
        if spec.default is not None:
            values.append(spec.default)
    for extra in (True, False, "", "on", "off", 1, 2, 0.0):
        if extra not in values:
            values.append(extra)
    return values


def enabling_values(spec: ParamSpec, template: dict) -> dict:
    """``{gating_key: value}`` changes that would make ``spec`` applicable.

    Only keys the predicate actually reads are considered, and only values that
    flip the predicate from false to true are returned. Empty when nothing in
    the template can enable the spec on its own.
    """
    fixes: dict = {}
    for key in dependencies_of(spec, template):
        for value in candidate_values(template, key):
            trial = dict(template)
            trial[key] = value
            if not spec.active(template) and spec.active(trial):
                fixes[key] = value
                break
    return fixes


def active_specs(template: dict):
    """The specs that apply to the template's current values, in table order."""
    return [spec for spec in PARAM_SPECS if spec.active(template)]


def is_default(spec: ParamSpec, value) -> bool:
    """True when ``value`` is llama-server's own default for ``spec``."""
    if value is None or value == "":
        return True
    if spec.default is None:
        return False
    return str(value) == str(spec.default)


def prune_defaults(template: dict, extra_keys=()) -> tuple:
    """Drop every llama-server default / inactive key from ``template``.

    Returns ``(template, removed_keys)``. ``extra_keys`` are never touched
    (they are template metadata such as ``name`` or ``model_path``).
    """
    removed = []
    for spec in PARAM_SPECS:
        if spec.key not in template:
            continue
        if not spec.active(template) or is_default(spec, template[spec.key]):
            removed.append(spec.key)
            del template[spec.key]
    return template, removed