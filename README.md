# dream

An SDK for building autonomous agent harnesses.

`dream` is the runtime layer of the [Arceus](https://github.com/Arceus-Inc)
stack. It exposes a `Harness` you construct, configure, and stream events
from. It owns the agent loop, tools, hooks, sandboxes, providers, and
sessions. It does not know about employees, companies, channels, or
strategy — those live in `chorus`, `lattice`, and `horizon`.

## Install

```pwsh
pip install dream
# or, with providers
pip install "dream[anthropic,openai,mcp]"
```

## Hello, harness

```python
import asyncio
from dream import Harness, HarnessConfig

async def main() -> None:
    async with Harness(HarnessConfig()) as h:
        session = await h.start_session()
        async for event in session.send("write a haiku about long-running agents"):
            print(event)
        print(session.cost)

asyncio.run(main())
```

## Design rules

1. One facade: `Harness`. Many instances per process. No globals.
2. Constructor injection. Env / file loading is an opt-in helper.
3. Async-first; sync facade lives in `dream.sync` and is thin.
4. All consumer output is typed `events.Event`. No prints, no logging
   side effects.
5. The public API is exactly what `dream/__init__.py` re-exports. Pinned
   by `tests/test_public_api.py`. Anything not re-exported may change.
6. Cross-repo contracts live in `dream.contracts` and have zero runtime
   dependencies, so `chorus` / `lattice` / `horizon` can depend on them
   without pulling in providers.

## Where things live

```
src/dream/
  __init__.py        # public API surface
  harness.py         # Harness facade
  session.py         # one conversation
  events.py          # typed event stream
  errors.py          # exception hierarchy
  contracts/         # cross-repo Protocols (zero deps)
  engine/            # private turn loop
  api/               # providers
  tools/builtin/     # built-in tools
  skills/  plugins/  hooks/  permissions/  sandbox/
  memory/  mcp/  swarm/  tasks/  services/
  prompts/  config/  state/  utils/
```

## License

MIT. See [LICENSE](LICENSE).
