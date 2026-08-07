"""Public construct-and-run factory — ``dream.build_harness``.

The one place that wires a *runnable* :class:`~dream.harness.Harness`: an
OpenAI-compatible streamer, the default tool registry, the permission gate,
task/cron bootstrap, skills, tracing, and auto-compaction — behind explicit
parameters instead of env-var coupling. SDK consumers construct through here.

Each ``start_session`` call constructs a fresh engine so per-session
``system_prompt`` / ``model`` overrides take effect; the ``ToolRegistry`` and
``AutoCompactState`` are shared so registrations and compaction-cooldown state
survive across sessions in the same process.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from dream.config.paths import DreamPaths
from dream.contracts.credentials import CredentialBrokerPort
from dream.contracts.plugin import Plugin
from dream.contracts.provider import ProviderCapabilities
from dream.engine._adapter_openai import (
    OpenAIChatStreamer,
    httpx_chat_completion_stream,
)
from dream.engine._engine import QueryEngine, build_query_engine
from dream.engine._failover_streamer import FailoverStreamer
from dream.engine._permission_gate import (
    compute_session_role_allowlist,
    make_permission_gate,
)
from dream.harness import AsyncOpener, AsyncTeardown, Harness, HarnessConfig
from dream.hooks import HookExecutor, collect_hooks
from dream.mcp import McpClientManager, mcp_paths, setup_mcp_session
from dream.memory import (
    MEMORY_CONTEXT_KEY,
    TASK_MEMORY_CONTEXT_KEY,
    FileMemoryStore,
    MemoryContext,
    TaskMemoryContext,
    WorkingMemory,
    project_memory_dir,
    proposals_dir,
    render_memory_catalogue,
    scan_memory_dir,
)
from dream.observability import JsonlTracer, TraceWriter
from dream.permissions import SessionLimits, read_sandbox_config
from dream.plugins import load_enabled_plugins
from dream.prompts.environment import render_runtime_info
from dream.roles import RoleManifest
from dream.runner._role_session import ROLE_MANIFEST_METADATA_KEY, ROLE_NAME_METADATA_KEY
from dream.sandbox import SANDBOX_CONTEXT_KEY, SandboxAdapter, select_backend
from dream.services import cron as cron_service
from dream.services.compact._carryover_state import CarryoverMetadata
from dream.services.compact._orchestrator import AutoCompactState
from dream.services.context_log import ContextEvent
from dream.services.core_beliefs import extract_standing_orders, render_standing_orders
from dream.session import SessionOptions
from dream.skills import (
    SKILL_CONTEXT_KEY,
    SkillContext,
    SkillRegistry,
    build_session_skill_registry,
    render_skill_catalogue,
)
from dream.subagents._async_delegation import AsyncDelegationManager
from dream.subagents._declaration import SubagentSet
from dream.tasks import (
    TASK_CONTEXT_KEY,
    BackgroundTaskManager,
    TaskSessionContext,
)
from dream.tasks._cron import CRON_MANIFEST_DIR, load_cron_manifests
from dream.tools._base import BaseTool
from dream.tools._registry import ToolRegistry, ToolSource
from dream.tools.builtin import default_registry, register_task_memory_tools
from dream.tools.builtin.spawn_subagent import SpawnSubagentTool

__all__ = ["PolicyWarningSink", "SkillEventSink", "build_harness"]

DEFAULT_BASE_URL = "https://api.openai.com/v1"

# A context-event sink the skill registry calls when a body loads.
SkillEventSink = Callable[[ContextEvent], None]

# A plain-string sink for operator-facing policy warnings (e.g. stale tier
# promotions) surfaced from permission-policy assembly at harness build.
PolicyWarningSink = Callable[[str], None]


def build_harness(
    *,
    model: str,
    api_key: str,
    base_url: str = DEFAULT_BASE_URL,
    working_dir: Path,
    max_turns: int = 8,
    registry: ToolRegistry | None = None,
    skill_registry: SkillRegistry | None = None,
    skills: bool = True,
    memory: bool = True,
    working_memory: bool = False,
    mcp: bool = True,
    plugins: bool = True,
    subagents: SubagentSet | None = None,
    skill_event_sink: SkillEventSink | None = None,
    policy_warning_sink: PolicyWarningSink | None = None,
    env: Mapping[str, str] | None = None,
    wake_model: str | None = None,
    credential_broker: CredentialBrokerPort | None = None,
) -> Harness:
    """Build a Harness whose engine factory produces a real, tool-wired engine.

    ``model`` / ``api_key`` / ``base_url`` name any OpenAI-compatible chat
    endpoint (vanilla OpenAI, Azure's ``/openai/v1`` path, vLLM, gateways).

    ``registry`` may be supplied so the caller can register additional tools
    (e.g. MCP adapters) into the same registry *after* this returns but
    *before* the first session starts — the tool wire-schema and the skill
    available-tool set are computed lazily per session, so late registrations
    are reflected.

    Skills are auto-discovered from the workspace (bundled + user + project
    ``SKILL.md`` dirs) by default so the whole action surface is wired with
    no caller effort. Pass ``skill_registry`` to supply your own (it wins);
    pass ``skills=False`` to disable discovery entirely.

    Workspace memory (the durable per-project record store under
    :func:`~dream.memory.project_memory_dir`) is wired by default: its
    catalogue lands in the system prompt and the ``memory_search`` /
    ``memory_get`` tools read from it. Pass ``memory=False`` to omit it.

    ``working_memory`` (default off) opts into the task-memory tier (spec 11a):
    it registers the ``working_memory_read`` / ``working_memory_write`` /
    ``working_memory_append`` scratchpad tools plus the outbound
    ``memory_propose`` seam, and wires a per-session
    :class:`~dream.memory.TaskMemoryContext` (a ``working-memory.md`` under the
    task sidecar + the durable ``_proposals/`` queue). Default-off keeps the
    standard tool surface byte-identical.

    ``mcp`` and ``plugins`` wire the two *async* action surfaces (Spec 06 / 13):
    the per-repo MCP allowlist is admitted, connected, and its tools registered;
    enabled repo-local plugins are loaded (tier-gated) and their tools / hooks /
    providers installed. Both run once, lazily, on the first ``start_session``
    (the async-open chokepoint) — never at construction — and tolerate a missing
    or empty config as "nothing to wire". Pass ``False`` to skip either surface.

    ``env`` is consulted only for host resolution — ``DREAM_HOME`` path
    overrides and shell detection for the runtime-info prompt block — and
    defaults to ``os.environ``. Credentials never come from it.

    ``wake_model`` is accepted for signature compatibility (chorus passes it)
    but is a no-op: the wake-cycle runtime was removed — chorus owns the loop.
    """
    if not model:
        raise ValueError("model must be a non-empty string")
    if not api_key:
        raise ValueError("api_key must be a non-empty string")
    resolved_env: Mapping[str, str] = env if env is not None else os.environ
    tool_registry = registry if registry is not None else default_registry()
    # Task memory (spec 11a) is opt-in: only register its tools when asked so
    # the default tool surface stays unchanged. The per-session context is
    # wired below in ``_build_session_engine`` (it needs the session id).
    if working_memory:
        register_task_memory_tools(tool_registry)
    # Subagents: register spawn when a set is provided (including empty — generalPurpose only).
    # ``subagents is None`` keeps the tool surface byte-identical (default off).
    if subagents is not None and tool_registry.get("spawn_subagent") is None:
        tool_registry.register(SpawnSubagentTool(), source=ToolSource.DEFAULT)
    compactor = AutoCompactState()
    # Resolve the home root from env so ``DREAM_HOME`` overrides are honoured
    # for task storage / sidecars (#43); hardcoding ``Path.home()`` would write
    # task artifacts under ~/.dream even when the operator redirected the root.
    paths = DreamPaths.resolve(working_dir, env=resolved_env).ensure()
    # Auto-discover workspace skills (Spec 06) unless the caller supplied a
    # registry or opted out. An explicit ``skill_registry`` wins — the REPL
    # builds its own with shadow reporting. Malformed SKILL.md files are the
    # boot gate's job to block (Runtime.run_boot_gates); the loader here is
    # tolerant so construction never raises on a bad skill.
    if skill_registry is None and skills:
        skill_registry, _shadows = build_session_skill_registry(working_dir, home=paths.home)
    task_manager, task_context = _bootstrap_task_and_cron(working_dir, paths)
    # Spec 13B: the sandbox *tier* (read-only/repo-write/...) is read from
    # ``.harness/sandbox.toml`` and enforced at the permission gate; the
    # *backend* is how approved commands execute. The adapter rides every
    # session's context_metadata so the ``bash`` tool runs through the one
    # execution mechanism instead of spawning its own.
    sandbox_adapter = _select_sandbox_adapter(paths)
    # Spec 13C policy-assembly warnings (e.g. stale tier promotions) are
    # operator-facing security signals; surface them once at build rather than
    # discarding them inside the factory (#47). They derive solely from
    # ``.harness/tool-tier-overrides.toml`` (paths) and are independent of
    # session/role, so one assembly here covers every session in this process.
    if policy_warning_sink is not None:
        _, policy_warnings = make_permission_gate(tool_registry, paths=paths, cwd=working_dir)
        for warning in policy_warnings:
            policy_warning_sink(warning)

    # 128K is the default we use throughout Spec 02; utilisation surfaces
    # (watch panel, /util) report against this number.
    capabilities = ProviderCapabilities(max_context_tokens=128_000)

    # Skills (Spec 06 slice 2): the frontmatter catalogue goes into the system
    # prompt so the model can discover skills; the SkillContext rides the
    # dispatcher's context_metadata so the `skill` tool can load bodies.
    catalogue = render_skill_catalogue(skill_registry.list_meta()) if skill_registry else ""
    # Memory (Spec 11): the read-side store over the per-project memory dir.
    # Its catalogue (id + description teasers) goes into the system prompt so
    # the model can discover durable facts; the MemoryContext rides the
    # dispatcher's context_metadata so the `memory_search` / `memory_get`
    # tools pull full records in. Disabled cleanly with ``memory=False``.
    memory_store = FileMemoryStore(project_memory_dir(paths.home, working_dir)) if memory else None
    memory_catalogue = (
        render_memory_catalogue(scan_memory_dir(memory_store.root))
        if memory_store is not None
        else ""
    )
    # Runtime environment (shell + OS + python) injected so the model picks the
    # right command syntax when it calls ``task_create command=...`` — without
    # this it guesses bash on Windows and cmd.exe rejects the command.
    runtime_info = render_runtime_info(env=resolved_env, working_dir=working_dir)

    # The task manager rides on the harness config so callers can register
    # lifecycle listeners and surface cron-spawned task starts/completions
    # alongside ordinary tool calls. The cron registry path rides alongside
    # so a scheduler tick loop knows where to poll, and `paths` carries the
    # env-resolved roots.
    del wake_model  # ponytail: compat no-op — the wake runtime is gone
    config = HarnessConfig(
        working_dir=working_dir,
        task_manager=task_manager,
        delegations=AsyncDelegationManager(),
        cron_registry_path=task_context.cron_registry_path,
        paths=paths,
        # MCP connect + plugin import are async/IO, so they hang off the
        # async-open chokepoint (``Harness._ensure_open``) rather than running
        # in this sync factory. ``None`` when both surfaces are disabled so the
        # chokepoint stays a no-op.
        _async_opener=(
            _make_async_opener(
                tool_registry=tool_registry,
                working_dir=working_dir,
                paths=paths,
                mcp=mcp,
                plugins=plugins,
            )
            if (mcp or plugins)
            else None
        ),
    )
    harness = Harness(config)

    # The engine factory closes over ``harness`` (not a hooks snapshot) so the
    # spec-13 lifecycle executor is assembled lazily at session construction
    # from ``harness._hooks`` / ``harness._plugins`` read *then* — hooks and
    # plugins registered via ``register_hook`` / ``register_plugin`` *after*
    # ``build_harness`` returns are still seen by the next session.
    def _factory(session_id: str, options: SessionOptions) -> QueryEngine:
        return _build_session_engine(
            session_id,
            options,
            tool_registry=tool_registry,
            paths=paths,
            working_dir=working_dir,
            api_key=api_key,
            base_url=base_url,
            model=model,
            max_turns=max_turns,
            catalogue=catalogue,
            memory_catalogue=memory_catalogue,
            runtime_info=runtime_info,
            skill_registry=skill_registry,
            skill_event_sink=skill_event_sink,
            memory_store=memory_store,
            working_memory=working_memory,
            task_context=task_context,
            sandbox_adapter=sandbox_adapter,
            compactor=compactor,
            capabilities=capabilities,
            harness=harness,
            subagents=subagents,
            credential_broker=credential_broker,
        )

    config._engine_factory = _factory
    return harness


def _make_async_opener(
    *,
    tool_registry: ToolRegistry,
    working_dir: Path,
    paths: DreamPaths,
    mcp: bool,
    plugins: bool,
) -> AsyncOpener:
    """Build the one-time async opener that wires MCP + plugins on first open.

    Runs inside ``Harness._ensure_open`` (before the first session's engine is
    built) so tools it registers are visible to that session's wire schema. It
    is deliberately tolerant: a missing/empty allowlist or plugins manifest is
    "nothing to wire", and a single plugin's failure never aborts the rest
    (spec 13 decision #22). Returns a teardown that closes the MCP manager, or
    ``None`` when there is nothing to tear down.
    """

    async def _opener(harness: Harness) -> AsyncTeardown | None:
        manager: McpClientManager | None = None
        if mcp:
            allowlist_path, credentials_path = mcp_paths(working_dir)
            setup = await setup_mcp_session(
                tool_registry,
                allowlist_path=allowlist_path,
                credentials_path=credentials_path,
            )
            # Blocking findings leave ``manager`` None and no tools registered —
            # the safe degradation: the session runs without MCP rather than
            # aborting. (The REPL surfaces these findings to the operator; here
            # the contract is non-fatal wiring.)
            manager = setup.manager
        if plugins:
            tier = read_sandbox_config(paths.sandbox_config()).tier
            report = load_enabled_plugins(working_dir, tier=tier)
            for plugin in report.loaded:
                _install_plugin(harness, tool_registry, plugin)
        if manager is None:
            return None

        async def _teardown() -> None:
            await manager.close()

        return _teardown

    return _opener


def _install_plugin(harness: Harness, tool_registry: ToolRegistry, plugin: Plugin) -> None:
    """Install one loaded plugin's contributions into the live harness.

    ``harness.register_plugin`` already records the bundle and attaches the
    plugin's hooks / providers (and stashes its tools on the harness). The one
    thing it does *not* do is put the tools in the engine-visible
    ``ToolRegistry`` — so the model would never see them. This function closes
    that gap: every plugin tool that is a real ``BaseTool`` joins the registry
    as ``PER_REPO`` (discovered, so it rides the trust ramp — untrusted until an
    operator promotes it, never auto-trusted like a built-in).

    A tool-name collision with an already-registered tool skips the *whole*
    plugin (so it never lands half-installed) rather than aborting the open —
    one bad plugin must not take down the others (spec 13 decision #22).
    """
    # Only concrete ``BaseTool`` instances carry the wire-schema / tier surface
    # the registry and permission gate need; a bare ``Tool``-protocol object
    # can't be rendered into the request, so it never reaches the engine.
    registrable = [t for t in plugin.tools if isinstance(t, BaseTool)]
    clash = next((t.name for t in registrable if t.name in tool_registry), None)
    if clash is not None:
        return
    for tool in registrable:
        tool_registry.register(tool, source=ToolSource.PER_REPO)
    # NB: ``plugin.skills`` are not surfaced in the system-prompt catalogue yet
    # (it is rendered once at build, before this opener runs); plugin tools /
    # hooks / providers are the live surface today.
    harness.register_plugin(plugin)


def _bootstrap_task_and_cron(
    working_dir: Path, paths: DreamPaths
) -> tuple[BackgroundTaskManager, TaskSessionContext]:
    """Build the per-harness task manager + cron context, seeding cron on disk.

    Task tools (Spec 07): one BackgroundTaskManager per harness, shared across
    sessions so task IDs / archives stay consistent. The cron registry lives at
    the in-repo convention (``.dream/cron/registry.json``); the exec-plans root
    is the parent of ``exec_plans_active`` since the FSM appends the state
    segment itself via :func:`plan_dir`. The two cron bootstrap calls are
    idempotent so operator edits to either the manifest or the registry survive
    restart.
    """
    task_manager = BackgroundTaskManager(tasks_dir=paths.tasks_dir)
    cron_registry_path = paths.dream_dir / "cron" / "registry.json"
    task_context = TaskSessionContext(
        manager=task_manager,
        cron_registry_path=cron_registry_path,
        plans_root=paths.exec_plans_active.parent,
    )
    cron_service.bootstrap_default_manifests(working_dir)
    cron_service.ensure_registry_seeded(
        cron_registry_path,
        load_cron_manifests(Path(working_dir) / CRON_MANIFEST_DIR),
    )
    return task_manager, task_context


def _select_sandbox_adapter(paths: DreamPaths) -> SandboxAdapter:
    """Pick the execution backend for this harness (Spec 13B).

    Reads ``.harness/sandbox.toml`` for tier and ``backend`` (default:
    docker). Subprocess is opt-in via ``backend = "subprocess"``; the
    backend is never inferred from the permission tier alone.

    When docker is selected, preflight ``get_docker_availability()``. If the
    daemon is unavailable and ``fail_if_unavailable`` is false, soft-degrade
    to subprocess (Spec 13); if true, raise :class:`SandboxError`.
    """
    from dream.errors import SandboxError
    from dream.sandbox import get_docker_availability

    cfg = read_sandbox_config(paths.sandbox_config())
    if cfg.backend == "subprocess":
        return select_backend("subprocess")

    availability = get_docker_availability()
    if availability.available:
        return select_backend("docker", docker=cfg.docker)

    reason = availability.reason or "Docker sandbox is unavailable"
    if cfg.docker.fail_if_unavailable:
        raise SandboxError(reason)
    return select_backend("subprocess")


def _assemble_system_prompt(
    *,
    paths: DreamPaths,
    runtime_info: str,
    catalogue: str,
    memory_catalogue: str,
    system_prompt: str | None,
) -> str:
    """Assemble the per-session system prompt from its ordered blocks.

    Order: the governance standing orders FIRST (the constitution outranks
    everything; Spec 13F AC #21-22, re-extracted every session start), then
    runtime info (host facts the model must trust), the skill catalogue
    (capabilities), the memory catalogue (durable workspace facts), and the
    caller-supplied prompt (task framing). Each block survives if the next is
    empty.
    """
    standing_orders = render_standing_orders(
        extract_standing_orders(paths.repo / "docs" / "design-docs" / "core-beliefs.md")
    )
    parts = [standing_orders] if standing_orders else []
    parts.append(runtime_info)
    if catalogue:
        parts.append(catalogue)
    if memory_catalogue:
        parts.append(memory_catalogue)
    if system_prompt:
        parts.append(system_prompt)
    return "\n\n".join(parts)


def _session_extra_params(
    tools_wire: list[dict[str, Any]], options: SessionOptions
) -> dict[str, Any] | None:
    """Build OpenAI request extras: tools + optional structured ``response_format``."""
    extra: dict[str, Any] = {}
    if tools_wire:
        extra["tools"] = tools_wire
        extra["tool_choice"] = "auto"
    if options.response_format is not None:
        extra["response_format"] = dict(options.response_format.to_openai())
    return extra or None


def _tool_advertised_to_model(*, name: str, role_allowed: frozenset[str] | None) -> bool:
    """Whether ``name`` belongs in the OpenAI ``tools`` wire for this session.

    When a role manifest stamped an allow-list (including empty — tool-less
    reformatter), only those tools are advertised. Unscoped sessions
    (``role_allowed is None``) keep the full registry.
    """
    return role_allowed is None or name in role_allowed


def _build_session_engine(
    session_id: str,
    options: SessionOptions,
    *,
    tool_registry: ToolRegistry,
    paths: DreamPaths,
    working_dir: Path,
    api_key: str,
    base_url: str,
    model: str,
    max_turns: int,
    catalogue: str,
    memory_catalogue: str,
    runtime_info: str,
    skill_registry: SkillRegistry | None,
    skill_event_sink: SkillEventSink | None,
    memory_store: FileMemoryStore | None,
    working_memory: bool,
    task_context: TaskSessionContext,
    sandbox_adapter: SandboxAdapter,
    compactor: AutoCompactState,
    capabilities: ProviderCapabilities,
    harness: Harness,
    subagents: SubagentSet | None = None,
    credential_broker: CredentialBrokerPort | None = None,
) -> QueryEngine:
    """Construct one session's ``QueryEngine`` from explicit, pre-resolved deps.

    Everything per-session (tool wire schema, skill context, prompt, permission
    gate, role allow-list, lifecycle hooks) is computed lazily here so tools
    *and* hooks/plugins registered after :func:`build_harness` (MCP adapters,
    ``register_hook`` / ``register_plugin`` etc.) are visible.
    """
    # Spec 13: assemble the lifecycle hook executor from the harness's *current*
    # hooks + plugins, read at session-construction time so late
    # ``register_hook`` / ``register_plugin`` calls are seen. ``collect_hooks``
    # merges harness-direct registrations (first) with plugin-contributed hooks
    # (in load order) deterministically. Built unconditionally — an empty hook
    # list makes ``fire`` a cheap no-op, so the firing seams stay live for a
    # later ``register_hook`` without rebuilding the harness. The executor's
    # ``emit`` is left defaulted (no-op): hook timeout/error event types are not
    # part of the OTel ``TraceEventType`` surface, so they don't ride the tracer.
    hook_executor = HookExecutor(collect_hooks(harness._hooks, harness._plugins))
    # Render the registry into OpenAI ``tools`` wire shape per session (cheap;
    # a handful of tools) so tools registered after build — MCP adapters /
    # resource + auth tools — are visible to the model. The engine's
    # TurnStreamer Protocol has no tools parameter (only messages), so we
    # smuggle the schema through ``httpx_chat_completion_stream``'s
    # ``extra_params`` — splatted verbatim into every request body.
    tools = tool_registry.list_tools()
    # Spec 10-H: when the caller stamped a RoleManifest on
    # ``options.metadata[ROLE_MANIFEST_METADATA_KEY]`` (the runner does
    # this in ``open_role_session``), intersect with the active sandbox
    # tier. Resolve the allow-list *before* rendering ``tools_wire`` so
    # tool-less roles (output-schema reformatter) do not advertise the
    # full registry alongside ``response_format``.
    manifest = options.metadata.get(ROLE_MANIFEST_METADATA_KEY)
    role_allowed: frozenset[str] | None = None
    if isinstance(manifest, RoleManifest):
        role_allowed = compute_session_role_allowlist(
            tool_registry, paths=paths, cwd=working_dir, manifest=manifest
        )
    # Effective set for schema enum (depth-2 children may inherit a scoped set).
    _schema_set = options.metadata.get("dream.subagent_set")
    if _schema_set is None:
        _schema_set = subagents
    tools_wire: list[dict[str, Any]] = []
    for t in tools:
        if not _tool_advertised_to_model(name=t.name, role_allowed=role_allowed):
            continue
        params = t.input_schema()
        if t.name == "spawn_subagent":
            from dream.tools.builtin.spawn_subagent import build_spawn_parameters

            params = build_spawn_parameters(params, _schema_set)
        tools_wire.append(
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": params,
                },
            }
        )
    # Built per session too, so the available-tool set the `skill` tool
    # checks ``tools_required`` against includes late (MCP) registrations.
    advertised = frozenset(
        t.name for t in tools if _tool_advertised_to_model(name=t.name, role_allowed=role_allowed)
    )
    skill_context = (
        SkillContext(
            registry=skill_registry,
            available_tools=advertised,
            event_sink=skill_event_sink,
        )
        if skill_registry is not None
        else None
    )
    system_prompt = _assemble_system_prompt(
        paths=paths,
        runtime_info=runtime_info,
        catalogue=catalogue,
        memory_catalogue=memory_catalogue,
        system_prompt=options.system_prompt,
    )
    # Failover harvest (2026-07-18): every beat's turn rides FailoverStreamer, so a 429/5xx
    # gets bounded retry instead of killing the beat. One substrate today — the rotation seam
    # activates when a second (name, streamer) pair is added here.
    streamer = FailoverStreamer(
        [
            (
                "primary",
                OpenAIChatStreamer(
                    stream_chat_completion=httpx_chat_completion_stream(
                        api_key=api_key,
                        base_url=base_url,
                        extra_params=_session_extra_params(tools_wire, options),
                    ),
                    model=options.model or model,
                    system_prompt=system_prompt,
                ),
            )
        ]
    )
    # OTel-shaped trace (Spec 12a): one durable JSONL per session under the
    # task sidecar. The session_id doubles as the sidecar dir key.
    tracer = JsonlTracer(
        # Reuse the env-resolved ``paths`` so the trace log honours
        # ``DREAM_HOME`` like task storage does (#43).
        TraceWriter(paths.trace_log(session_id)),
        session_id=session_id,
        task_id=session_id,
    )
    # Spec 13C: gate every tool call against the sandbox policy assembled
    # from the registry's declared tiers + operator .harness config. Stale
    # promotions etc. surface as warnings (data); not emitted here yet.
    # SECURITY: do NOT feed ``role_allowed`` into the gate's ``tool_allow``.
    # ``tool_allow`` is an allow-list override (it lets a tool bypass the
    # tool-deny list), so passing role tools there would *widen* them rather
    # than restrict them. Role enforcement is a hard "must be in set" deny in
    # the dispatcher (``role_allowed_tools`` below); the gate then applies its
    # full pipeline (path/command deny, tier, trust) to every role-allowed
    # tool. See ``compute_session_role_allowlist``'s docstring for the
    # rationale.
    permission_gate, _gate_warnings = make_permission_gate(
        tool_registry, paths=paths, cwd=working_dir
    )
    # Dispatcher context_metadata: skill + task contexts keyed for the
    # `skill` / task tools to fetch out of the dispatcher, plus the sandbox
    # adapter the `bash` tool routes execution through (Spec 13B).
    from dream.tools.execute_code import EXECUTE_CODE_REGISTRY_KEY

    context_metadata: dict[str, Any] = {
        TASK_CONTEXT_KEY: task_context,
        SANDBOX_CONTEXT_KEY: sandbox_adapter,
        # execute_code nested RPC dispatches through the live session registry.
        EXECUTE_CODE_REGISTRY_KEY: tool_registry,
    }
    if ROLE_NAME_METADATA_KEY in options.metadata:
        context_metadata[ROLE_NAME_METADATA_KEY] = options.metadata[ROLE_NAME_METADATA_KEY]
    from dream.subagents._inline_executor import SUBAGENT_NAME_METADATA_KEY

    if SUBAGENT_NAME_METADATA_KEY in options.metadata:
        context_metadata[SUBAGENT_NAME_METADATA_KEY] = options.metadata[SUBAGENT_NAME_METADATA_KEY]
    if skill_context is not None:
        context_metadata[SKILL_CONTEXT_KEY] = skill_context
    if memory_store is not None:
        context_metadata[MEMORY_CONTEXT_KEY] = MemoryContext(store=memory_store)
    # Task memory (spec 11a): a ``working-memory.md`` under the task sidecar
    # (so it dies with the worktree, #02) plus the durable ``_proposals/`` queue
    # in the dream home (so a proposal survives that teardown). Wired only when
    # ``working_memory=True`` registered the tools; the session_id keys the
    # sidecar dir, matching the tracer above.
    if working_memory:
        context_metadata[TASK_MEMORY_CONTEXT_KEY] = TaskMemoryContext(
            working_memory=WorkingMemory(paths.sidecar(session_id) / "working-memory.md"),
            proposals_dir=proposals_dir(paths.home, working_dir),
            source_ref=f"session://{session_id}",
        )
    # Subagents: wire the SubagentSet + harness reference into context_metadata
    # so the spawn_subagent tool can create real bounded sessions.
    #
    # Depth-2 inheritance: a spawned child that may itself spawn is handed a *scoped* subagent set
    # and the *parent's* spawn counter on ``options.metadata`` (the inline executor sets these).
    # When present they take precedence over the harness defaults, so the child spawns only what it
    # was scoped to and the per-beat cap spans the whole tree. Top-level sessions carry no such keys.
    from dream.tools.builtin.spawn_subagent import (
        HARNESS_KEY,
        OBSERVER_KEY,
        PARENT_PERMISSIONS_KEY,
        PARENT_TOOLS_KEY,
        SPAWN_COUNT_KEY,
        SPAWN_LEDGER_KEY,
        SUBAGENT_SET_CONTEXT_KEY,
        TRACER_KEY,
        SpawnLedger,
    )

    # Role allowlist + permission gate must be visible to execute_code even when
    # the session has no subagent set (top-level role without spawn capability).
    if role_allowed is not None:
        context_metadata[PARENT_TOOLS_KEY] = role_allowed
    context_metadata[PARENT_PERMISSIONS_KEY] = permission_gate

    # The run_role observer (when present) rides into the tool context, so the spawn tool can
    # forward it into a child session — nested spawns then surface on the same observer/bus.
    if OBSERVER_KEY in options.metadata:
        context_metadata[OBSERVER_KEY] = options.metadata[OBSERVER_KEY]

    inherited_set = options.metadata.get(SUBAGENT_SET_CONTEXT_KEY)
    effective_subagents = inherited_set if inherited_set is not None else subagents
    # Wire even when empty so generalPurpose can run without Spec templates.
    if effective_subagents is not None:
        context_metadata[SUBAGENT_SET_CONTEXT_KEY] = effective_subagents
        context_metadata[TRACER_KEY] = tracer
        context_metadata[HARNESS_KEY] = harness
        # Per-session spawn counter — inherit the parent's when spawned from a child (so the per-beat
        # cap spans the whole tree), else seed fresh (per-beat, never accumulating on the shared tool
        # instance — the cross-session DoS).
        context_metadata[SPAWN_COUNT_KEY] = options.metadata.get(SPAWN_COUNT_KEY, [0])
        context_metadata[SPAWN_LEDGER_KEY] = options.metadata.get(SPAWN_LEDGER_KEY, SpawnLedger())
    carryover_metadata = CarryoverMetadata.for_working_dir(str(working_dir))
    from dream.services.compact._summariser import make_llm_summariser

    compaction_summariser = make_llm_summariser(
        api_key=api_key,
        base_url=base_url,
        model=options.model or model,
        state=carryover_metadata,
    )
    return build_query_engine(
        streamer=streamer,
        registry=tool_registry,
        session_id=session_id,
        working_dir=working_dir,
        scratch_dir=paths.sidecar(session_id) / "scratch",
        max_turns=options.max_turns or max_turns,
        permission_gate=permission_gate,
        role_allowed_tools=role_allowed,
        limits=SessionLimits(),
        context_metadata=context_metadata,
        compactor=compactor,
        compaction_capabilities=capabilities,
        compaction_summariser=compaction_summariser,
        carryover_metadata=carryover_metadata,
        tracer=tracer,
        model=options.model or model,
        hook_executor=hook_executor,
        delegations=harness.config.delegations,
        credential_broker=credential_broker,
    )
