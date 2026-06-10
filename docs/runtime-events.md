# Runtime event catalogue (spec 15 P2)

The runtime's outbound API is one JSONL stream at
`.dream/runtime/events.jsonl` (rotated once past `RuntimeConfig.events_max_bytes`
to `events.jsonl.1`; exactly one prior generation). Read it with
`dream.tail_events(path)` or `python -m dream.ctl events [--last N]`.

Every record carries three reserved fields written by the sink — `ts`
(ISO8601, milliseconds), `pid` (writer process), `type` (discriminator) —
plus the type-specific payload below. Consumers MUST ignore unknown event
types and unknown payload keys: the catalogue only grows.

## Lifecycle

| type | payload | meaning |
|---|---|---|
| `runtime.started` | `agent_id`, `loops: [str]`, `resume_candidates: int` | boot finished; loops are live |
| `runtime.stopped` | `agent_id` | graceful shutdown completed |
| `runtime.boot.warning` | `code`, `message`, `path` (or `task_id` for `corrupt_sidecar`) | advisory boot finding (structural validator, corrupt sidecar) |
| `runtime.boot.blocked` | `findings: [str]` | a blocking gate refused boot (skills, threat scan) |
| `runtime.resume.candidate` | `task_id`, `base_branch`, `last_checkpoint_turn` | a sidecar still marked `running` from a previous process |

## Supervision

| type | payload | meaning |
|---|---|---|
| `runtime.health` | `loop`, `error`, `restarts` | a supervised loop crashed and will restart |
| `runtime.loop.abandoned` | `loop`, `restarts` | crash ceiling hit; the loop will NOT restart |

## Background tasks (mirrored from the task manager)

| type | payload | meaning |
|---|---|---|
| `runtime.task.started` | `task_id`, `description` | background task spawned (cron or `task_create`) |
| `runtime.task.finished` | `task_id`, `status`, `return_code` | background task reached a terminal state |
| `runtime.drain.stopped_task` | `task_id`, `status` | shutdown drain timed out and stopped this task |

## Jobs (submitted end-to-end tasks)

| type | payload | meaning |
|---|---|---|
| `runtime.job.finished` | `task_id` | a submitted `run_task` sprint loop completed |
| `runtime.job.failed` | `task_id`, `error` | the job raised, exhausted its retries, or hit its wall-clock budget |
| `runtime.job.retry` | `task_id`, `attempt`, `error` | a failed job is being retried (`RuntimeConfig.job_max_retries`) |
| `runtime.job.cancelled` | `task_id` | cancelled by command or shutdown |

## Supervised swarm workers (spec 15 P5)

| type | payload | meaning |
|---|---|---|
| `runtime.worker.started` | `agent_id`, `task_id`, `team`, `restarts` | a teammate child process spawned (restarts counts prior crashes) |
| `runtime.worker.finished` | `agent_id`, `task_id` | the worker exited cleanly (rc=0) |
| `runtime.worker.exited` | `agent_id`, `status`, `return_code`, `restarts` | the worker failed/was killed; will restart if under the cap |
| `runtime.worker.spawn_failed` | `name`, `team`, `error` | the executor refused or failed the spawn (includes bridge refusals) |
| `runtime.worker.cancelled` | `agent_id` | supervision cancelled (shutdown); child stopped |
| `runtime.worker.abandoned` | `name`, `team`, `restarts` | restart ceiling hit; the worker will NOT be respawned |

## Liveness watchdog (spec 10p5)

| type | payload | meaning |
|---|---|---|
| `runtime.watchdog.stale_claim` | `task_id`, `state`, `claimed_by`, `lease_expires_at_ms`, `expired_for_ms` | a claimed/executing board row whose lease expired — the owning runner died or wedged; emitted once per lease epoch |

## Wake cycle (spec 06.5, forwarded by the scheduler / wake command)

| type | payload | meaning |
|---|---|---|
| `heartbeat.decision.run` / `.skip` / `.forced` | `agent_id`, `action`, `tasks`, `reason`, `forced`, `outcome`, `decided_at`, `wake_source` | one wake decision |
| `heartbeat.missing` | same | the model produced no usable decision |
| `wake.dropped` | `agent_id`, `reason`, `wake_source` | another wake held the per-agent lock |
| `runtime.wake.run` | `agent_id`, `tasks: [str]`, `reason`, `forced` | a `run` decision surfaced to consumers |

## Command channel

| type | payload | meaning |
|---|---|---|
| `runtime.command.ack` | `command_id`, `status: ok\|error\|rejected`, `summary`, `next_actions: [str]`, `artifacts: [str]` | reply to one inbox command — the observation contract every surface shares |

## Commands (inbound, `.dream/runtime/inbox/*.json`)

Written atomically by `python -m dream.ctl` or `dream.channels.CommandInbox`:
`submit_task {intent, task_id?, max_sprints?}` · `cancel {task_id}` ·
`status {}` · `wake {}`. Each carries `id` (the ack correlation key) and
`timestamp`.
