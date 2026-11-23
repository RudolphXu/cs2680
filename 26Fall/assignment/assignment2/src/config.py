"""Configuration: environment variables and tunable constants.

Must stay compatible with Python 3.9.
"""

import os

# --- model endpoint -------------------------------------------------------
DEFAULT_BASE_URL = "https://api.cs2680.com/v1"
DEFAULT_MODEL = "qwen3.6-35b-a3b"

# Provider routing is an OpenRouter-only request field; the course proxy
# never receives it. Slugs verified against GET /api/v1/providers
# (Parasail -> parasail, DeepInfra -> deepinfra, PrimeIntellect ->
# primeintellect). A base slug matches all of that provider's endpoints
# (e.g. deepinfra/fp8, deepinfra/fp8/flex). These three are tried first;
# when all are down, OpenRouter may fall back to other providers, but only
# fp8 endpoints that support every request parameter (tools): that still
# excludes Darkbloom (fp4) and CoreWeave / SiliconFlow (no tools).
OPENROUTER_PROVIDERS = ["parasail", "deepinfra", "primeintellect"]
OPENROUTER_PROVIDER = {
    "order": OPENROUTER_PROVIDERS,
    "allow_fallbacks": True,
    "require_parameters": True,
    "quantizations": ["fp8"],
}

# Environment variables that carry secrets; stripped from the bash tool env.
SECRET_ENV_VARS = ("MADSLOOP_API_KEY", "CS2680_API_KEY", "OPENROUTER_API_KEY")

# Where docker_env.sh installs the harness dependencies; removed from the
# bash tool's PYTHONPATH so repo tests run in the task's own environment.
HARNESS_DEPS_DIR = "/opt/madsloop_deps"


class ModelConfig(object):
    def __init__(self, base_url, api_key, model):
        self.base_url = base_url
        self.api_key = api_key
        self.model = model

    @property
    def is_openrouter(self):
        return "openrouter.ai" in (self.base_url or "")


def load_model_config(environ=None):
    env = os.environ if environ is None else environ
    base_url = env.get("MADSLOOP_BASE_URL") or DEFAULT_BASE_URL
    model = env.get("MADSLOOP_MODEL") or DEFAULT_MODEL
    api_key = env.get("MADSLOOP_API_KEY") or env.get("CS2680_API_KEY") or ""
    return ModelConfig(base_url, api_key, model)


# --- tools ---------------------------------------------------------------
BASH_DEFAULT_TIMEOUT = 120
BASH_MAX_TIMEOUT = 600
READ_DEFAULT_MAX_LINES = 400

# --- context -------------------------------------------------------------
TOOL_OUTPUT_MAX_CHARS = 10000
COMPACT_TOKEN_THRESHOLD = 100000
COMPACT_KEEP_RECENT_TURNS = 6
COMPACTED_PLACEHOLDER = "[earlier output omitted; re-run the command if you need it]"

# --- loop ----------------------------------------------------------------
MAX_ITERATIONS = 80
NEAR_LIMIT_WARNING = 10          # remind the model this many iterations before the cap
MAX_NO_TOOL_REPLIES = 3          # consecutive replies without tool calls before stopping
MAX_DONE_REJECTIONS = 2          # empty-diff done calls rejected before accepting
TIME_LIMIT_S = 45 * 60

# --- API retries ---------------------------------------------------------
API_MAX_RETRIES = 8
API_BACKOFF_BASE_S = 1.0
API_BACKOFF_MAX_S = 60.0
API_TIMEOUT_S = 600.0
