# dream — consumer-facing API

Everything an SDK consumer needs to build agents on `dream`, in one place.

| File | What it is |
|---|---|
| [QUICKSTART.md](QUICKSTART.md) | Zero to a running `run_task()` in five minutes. |
| [SDK_GUIDE.md](SDK_GUIDE.md) | The full guide: every component, the task loop, security model, conventions. |
| [run_harness.py](run_harness.py) | Configurable runner — toggle every component from the command line. |
| [examples/](examples/) | One runnable script per component, plus an everything-at-once demo. |

## The 10-second version

```python
from dream import build_harness

harness = build_harness(
    model="gpt-5.2",
    api_key="...",
    base_url="https://api.openai.com/v1",   # any OpenAI-compatible endpoint
    working_dir=Path("./my-workspace"),      # a git repo
)

async with harness:
    result = await harness.run_task(intent="Build a CLI that ...")
```

One call constructs a fully-wired harness — skills, workspace memory, MCP
servers, plugins, sandboxed shell, lifecycle hooks — and `run_task` drives a
planner → sprint → evaluator loop until the task is done or blocked.

## Examples index

| Script | Component | Shows |
|---|---|---|
| `examples/01_minimal_run_task.py` | core | The smallest possible end-to-end task. |
| `examples/02_skills.py` | skills | A workspace `SKILL.md` rule the agent discovers and applies. |
| `examples/03_memory.py` | memory | A fact stored only in project memory, retrieved via `memory_search`/`memory_get`. |
| `examples/04_plugins.py` | plugins | A repo-local plugin contributing a custom tool the agent calls. |
| `examples/05_mcp.py` | MCP | Wiring a real MCP server (Playwright) through the allowlist + trust ramp. |
| `examples/06_hooks_and_observer.py` | hooks / observability | Lifecycle hooks firing inside the engine + streaming run events. |
| `examples/07_custom_heads.py` | orchestration | Overriding a head (custom evaluator) while the rest stays stock. |
| `examples/08_full_surface.py` | everything | All components in a single `run_task`. |

Each example is standalone:

```bash
export DREAM_MODEL=... DREAM_API_KEY=... DREAM_BASE_URL=...
uv run python consumer-facing-api/examples/01_minimal_run_task.py
```

Or drive everything from one CLI:

```bash
uv run python consumer-facing-api/run_harness.py \
    --intent "Create hello.py that prints hello" \
    --workspace /tmp/demo --no-mcp --sandbox-tier repo-write
```
