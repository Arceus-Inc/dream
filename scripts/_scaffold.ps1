# Scaffold the dream SDK package tree.
#
# Idempotent: safe to re-run. Creates directories and stub modules with
# one-line docstrings describing each file's job. Substantive files
# (pyproject, README, contracts/*.py, public __init__.py, tests) are
# authored separately, not generated here.

[CmdletBinding()]
param(
    [string]$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
)

$ErrorActionPreference = 'Stop'

function New-Dir([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) {
        New-Item -ItemType Directory -Path $Path -Force | Out-Null
    }
}

function New-Stub([string]$Path, [string]$Doc) {
    if (Test-Path -LiteralPath $Path) { return }
    $body = "`"`"`"$Doc`"`"`"`r`n"
    Set-Content -LiteralPath $Path -Value $body -Encoding UTF8 -NoNewline
}

function New-PkgInit([string]$Dir, [string]$Doc) {
    New-Stub (Join-Path $Dir '__init__.py') $Doc
}

$src = Join-Path $Root 'src/dream'

# ---------------------------------------------------------------------------
# Top-level package directories (intent-only stubs; real code lands later).
# ---------------------------------------------------------------------------

$packages = @{
    ''             = 'dream - an SDK for building autonomous agent harnesses.'
    'contracts'    = 'Cross-repo Protocols and types. Zero runtime dependencies.'
    'engine'       = 'Private: the turn loop. Not part of the public API.'
    'api'          = 'Provider implementations (anthropic, openai-compatible).'
    'tools'        = 'Tool registry and built-in tools.'
    'tools/builtin'= 'Built-in tools shipped with the SDK.'
    'skills'       = 'Skill loader, registry, and bundled markdown playbooks.'
    'skills/bundled' = 'Markdown skills shipped with the SDK.'
    'plugins'      = 'Plugin loader and installer.'
    'plugins/bundled' = 'Reference plugins shipped with the SDK.'
    'hooks'        = 'Hook registry and executor (block opt-in via HookSpec).'
    'permissions'  = 'Permission modes: DEFAULT, PLAN, FULL_AUTO, STRICT.'
    'sandbox'      = 'Sandbox adapters (subprocess default, docker optional).'
    'memory'       = 'Memory read side. Writes happen via MemoryWriter Protocol.'
    'mcp'          = 'Model Context Protocol client.'
    'swarm'        = 'Task-level subagents (Harness.spawn_agent). Not org-level.'
    'tasks'        = 'Background task manager.'
    'services'     = 'Cross-cutting subsystems: cron, session_storage, compactor.'
    'services/compact' = 'History compaction strategies.'
    'prompts'      = 'System prompt assembly.'
    'config'       = 'Opt-in helpers for loading HarnessConfig from env/file/dict.'
    'state'        = 'Per-Harness runtime state. Never global.'
    'utils'        = 'Small leaf utilities: file_lock, atomic_write, network_guard.'
    '_internal'    = 'Internal dumping ground. Nothing here is stable.'
}

foreach ($rel in $packages.Keys) {
    $dir = if ($rel) { Join-Path $src $rel } else { $src }
    New-Dir $dir
    New-PkgInit $dir $packages[$rel]
}

# ---------------------------------------------------------------------------
# Top-level public modules (real bodies authored separately).
# Placeholders ensure imports succeed during scaffold verification.
# ---------------------------------------------------------------------------

$publicModules = @{
    'harness.py' = 'Harness facade. The single entry point to the SDK runtime.'
    'session.py' = 'Session: one conversation on a Harness.'
    'events.py'  = 'Public typed events streamed to SDK consumers.'
    'errors.py'  = 'Public exception hierarchy with stable string codes.'
    'types.py'   = 'Public type aliases shared across the surface.'
    'sync.py'    = 'Optional sync facade wrapping the async Harness.'
}

foreach ($name in $publicModules.Keys) {
    New-Stub (Join-Path $src $name) $publicModules[$name]
}

# ---------------------------------------------------------------------------
# Private engine modules (leading underscore -> internal-by-convention).
# ---------------------------------------------------------------------------

$engineModules = @{
    '_engine.py'   = 'Internal QueryEngine. Owned by Session, not exported.'
    '_loop.py'     = 'Internal turn-loop coroutine. Pure orchestration.'
    '_messages.py' = 'Internal Message/ContentBlock types. Converted at boundary.'
    '_cost.py'     = 'Internal cost tracker. Exposed via Session.cost.'
}
$engineDir = Join-Path $src 'engine'
foreach ($name in $engineModules.Keys) {
    New-Stub (Join-Path $engineDir $name) $engineModules[$name]
}

# ---------------------------------------------------------------------------
# api/ providers.
# ---------------------------------------------------------------------------

$apiModules = @{
    '_registry.py' = 'Provider name -> factory registry.'
    '_client.py'   = 'Default-provider resolution and retry policy.'
    '_pricing.py'  = 'Model -> cost mapping loader.'
    'anthropic.py' = 'Anthropic Messages API provider implementation.'
    'openai.py'    = 'OpenAI-compatible provider (Azure, LiteLLM, vLLM, Ollama).'
}
$apiDir = Join-Path $src 'api'
foreach ($name in $apiModules.Keys) {
    New-Stub (Join-Path $apiDir $name) $apiModules[$name]
}

# ---------------------------------------------------------------------------
# tools/
# ---------------------------------------------------------------------------

$toolsModules = @{
    '_base.py'     = 'BaseTool ABC for built-in tools.'
    '_registry.py' = 'ToolRegistry: collision rules, permission-aware listing.'
    '_context.py'  = 'Internal ToolContext implementation.'
}
$toolsDir = Join-Path $src 'tools'
foreach ($name in $toolsModules.Keys) {
    New-Stub (Join-Path $toolsDir $name) $toolsModules[$name]
}

$builtinTools = @{
    'bash.py'        = 'Shell execution tool, routed through the sandbox adapter.'
    'file_read.py'   = 'Read a file, gated by path_validator.'
    'file_write.py'  = 'Write a file, gated by path_validator and permissions.'
    'file_edit.py'   = 'In-place edit with diff preview.'
    'glob.py'        = 'Glob-based file search.'
    'grep.py'        = 'Content search across the working tree.'
    'web_fetch.py'   = 'HTTP GET with network_guard allowlist.'
    'todo_write.py'  = 'Persist the agent''s plan to Session state.'
}
$builtinDir = Join-Path $src 'tools/builtin'
foreach ($name in $builtinTools.Keys) {
    New-Stub (Join-Path $builtinDir $name) $builtinTools[$name]
}

# ---------------------------------------------------------------------------
# skills/
# ---------------------------------------------------------------------------

$skillsModules = @{
    '_loader.py'      = 'Discover skills from bundled, user, project, and plugin sources.'
    '_registry.py'    = 'Name -> SkillDefinition lookup with collision rules.'
    '_frontmatter.py' = 'YAML frontmatter parser for skill markdown files.'
}
$skillsDir = Join-Path $src 'skills'
foreach ($name in $skillsModules.Keys) {
    New-Stub (Join-Path $skillsDir $name) $skillsModules[$name]
}

# ---------------------------------------------------------------------------
# plugins/
# ---------------------------------------------------------------------------

$pluginsModules = @{
    '_loader.py'    = 'Discover plugins from user and project directories.'
    '_installer.py' = 'Install plugins from a git URL or local path.'
    '_schemas.py'   = 'Pydantic models for plugin.json.'
}
$pluginsDir = Join-Path $src 'plugins'
foreach ($name in $pluginsModules.Keys) {
    New-Stub (Join-Path $pluginsDir $name) $pluginsModules[$name]
}

# ---------------------------------------------------------------------------
# hooks/
# ---------------------------------------------------------------------------

$hooksModules = @{
    '_executor.py' = 'Priority-sorted hook dispatch with opt-in blocking.'
    '_loader.py'   = 'Merge hooks from settings and plugin contributions.'
}
$hooksDir = Join-Path $src 'hooks'
foreach ($name in $hooksModules.Keys) {
    New-Stub (Join-Path $hooksDir $name) $hooksModules[$name]
}

# ---------------------------------------------------------------------------
# permissions/
# ---------------------------------------------------------------------------

$permModules = @{
    'modes.py'         = 'PermissionMode enum: DEFAULT, PLAN, FULL_AUTO, STRICT.'
    '_checker.py'      = 'Decision engine: mode + allow/deny + path rules + commands.'
    '_path_validator.py' = 'Glob-based path allow/deny with gitignore-style negation.'
}
$permDir = Join-Path $src 'permissions'
foreach ($name in $permModules.Keys) {
    New-Stub (Join-Path $permDir $name) $permModules[$name]
}

# ---------------------------------------------------------------------------
# sandbox/
# ---------------------------------------------------------------------------

$sandboxModules = @{
    '_adapter.py'             = 'SandboxAdapter Protocol: argv -> ProcessResult.'
    '_session.py'             = 'Per-Session sandbox lifecycle (no module-level state).'
    'subprocess_backend.py'   = 'Default adapter: asyncio subprocess with scrubbed env.'
    'docker_backend.py'       = 'Optional adapter: container with mounts and limits.'
}
$sandboxDir = Join-Path $src 'sandbox'
foreach ($name in $sandboxModules.Keys) {
    New-Stub (Join-Path $sandboxDir $name) $sandboxModules[$name]
}

# ---------------------------------------------------------------------------
# memory/  (read side only - writes go through MemoryWriter Protocol)
# ---------------------------------------------------------------------------

$memoryModules = @{
    '_paths.py' = 'Resolve memory file locations (user, project, plugin scopes).'
    '_scan.py'  = 'Discover and parse MEMORY.md files on session start.'
    '_search.py' = 'Substring and frontmatter search over memory records.'
}
$memoryDir = Join-Path $src 'memory'
foreach ($name in $memoryModules.Keys) {
    New-Stub (Join-Path $memoryDir $name) $memoryModules[$name]
}

# ---------------------------------------------------------------------------
# mcp/
# ---------------------------------------------------------------------------

$mcpModules = @{
    '_client.py' = 'MCP client: spawn or connect to servers, register their tools.'
    '_config.py' = 'Load MCP server definitions from settings and .mcp.json.'
}
$mcpDir = Join-Path $src 'mcp'
foreach ($name in $mcpModules.Keys) {
    New-Stub (Join-Path $mcpDir $name) $mcpModules[$name]
}

# ---------------------------------------------------------------------------
# swarm/  (task-level subagents only)
# ---------------------------------------------------------------------------

$swarmModules = @{
    '_registry.py'           = 'Agent definition registry for the local Harness.'
    '_mailbox.py'            = 'Async message passing between parent and subagents.'
    '_worktree.py'           = 'git worktree helpers for branch-isolated subagents.'
    'subprocess_backend.py'  = 'Spawn subagent as child process with JSON-line framing.'
    'in_process.py'          = 'Spawn subagent as asyncio task sharing the parent factory.'
}
$swarmDir = Join-Path $src 'swarm'
foreach ($name in $swarmModules.Keys) {
    New-Stub (Join-Path $swarmDir $name) $swarmModules[$name]
}

# ---------------------------------------------------------------------------
# tasks/
# ---------------------------------------------------------------------------

$tasksModules = @{
    '_manager.py' = 'BackgroundTaskManager: launch, poll, list, cancel.'
}
$tasksDir = Join-Path $src 'tasks'
foreach ($name in $tasksModules.Keys) {
    New-Stub (Join-Path $tasksDir $name) $tasksModules[$name]
}

# ---------------------------------------------------------------------------
# services/
# ---------------------------------------------------------------------------

$servicesModules = @{
    'session_storage.py'   = 'Default SessionStore: append-only JSONL on disk.'
    'cron.py'              = 'croniter-driven scheduler with file-locked registry.'
    'exec_plan.py'         = 'Default ExecPlanLedger: file-backed with locking.'
    'token_estimation.py'  = 'Cheap tokeniser for compaction triggers.'
    'tool_outputs.py'      = 'Spill large tool outputs to disk, return file refs.'
}
$servicesDir = Join-Path $src 'services'
foreach ($name in $servicesModules.Keys) {
    New-Stub (Join-Path $servicesDir $name) $servicesModules[$name]
}

$compactModules = @{
    '_compactor.py' = 'History compaction: summarise oldest N turns into one message.'
}
$compactDir = Join-Path $src 'services/compact'
foreach ($name in $compactModules.Keys) {
    New-Stub (Join-Path $compactDir $name) $compactModules[$name]
}

# ---------------------------------------------------------------------------
# prompts/
# ---------------------------------------------------------------------------

$promptsModules = @{
    'system_prompt.py' = 'Assemble the final system prompt from base + context + skills + memory.'
    'context.py'       = 'Working-dir, git, recent-files context blocks.'
    'environment.py'   = 'Time, user, hostname, model-name context.'
}
$promptsDir = Join-Path $src 'prompts'
foreach ($name in $promptsModules.Keys) {
    New-Stub (Join-Path $promptsDir $name) $promptsModules[$name]
}

# ---------------------------------------------------------------------------
# config/  (opt-in helpers; SDK never reads env/disk unless asked)
# ---------------------------------------------------------------------------

$configModules = @{
    'from_env.py'  = 'Build a HarnessConfig from environment variables.'
    'from_file.py' = 'Build a HarnessConfig from a settings.json file.'
    'from_dict.py' = 'Build a HarnessConfig from a plain dict.'
    'paths.py'     = 'Resolve standard data/log/session directories on demand.'
}
$configDir = Join-Path $src 'config'
foreach ($name in $configModules.Keys) {
    New-Stub (Join-Path $configDir $name) $configModules[$name]
}

# ---------------------------------------------------------------------------
# state/
# ---------------------------------------------------------------------------

$stateModules = @{
    'store.py' = 'RuntimeStore: per-Harness live state (todos, mode, sandbox handle).'
}
$stateDir = Join-Path $src 'state'
foreach ($name in $stateModules.Keys) {
    New-Stub (Join-Path $stateDir $name) $stateModules[$name]
}

# ---------------------------------------------------------------------------
# utils/
# ---------------------------------------------------------------------------

$utilsModules = @{
    'file_lock.py'     = 'Cross-platform exclusive file lock context manager.'
    'fs.py'            = 'atomic_write_text, safe_rename, mtime helpers.'
    'shell.py'         = 'OS-aware argv quoting and execution helpers.'
    'network_guard.py' = 'Domain allow/deny enforcement for web/network tools.'
}
$utilsDir = Join-Path $src 'utils'
foreach ($name in $utilsModules.Keys) {
    New-Stub (Join-Path $utilsDir $name) $utilsModules[$name]
}

# ---------------------------------------------------------------------------
# tests/ - mirror structure, one starter file per area.
# ---------------------------------------------------------------------------

$testsDir = Join-Path $Root 'tests'
New-Dir $testsDir
New-Stub (Join-Path $testsDir '__init__.py') 'dream test suite.'
New-Stub (Join-Path $testsDir 'conftest.py') 'Shared pytest fixtures.'

$testAreas = @(
    'test_contracts', 'test_engine', 'test_api', 'test_tools',
    'test_skills', 'test_plugins', 'test_hooks', 'test_permissions',
    'test_sandbox', 'test_memory', 'test_mcp', 'test_swarm',
    'test_tasks', 'test_services', 'test_prompts', 'test_config',
    'test_state', 'test_utils'
)
foreach ($area in $testAreas) {
    $dir = Join-Path $testsDir $area
    New-Dir $dir
    New-PkgInit $dir "Tests for the $($area.Substring(5)) package."
}

# ---------------------------------------------------------------------------
# py.typed marker (PEP 561) so consumers get type info.
# ---------------------------------------------------------------------------

$pytyped = Join-Path $src 'py.typed'
if (-not (Test-Path -LiteralPath $pytyped)) {
    Set-Content -LiteralPath $pytyped -Value '' -Encoding UTF8 -NoNewline
}

Write-Host "Scaffold complete under: $Root"
