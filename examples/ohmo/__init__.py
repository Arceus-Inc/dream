"""ohmo — an always-on research agent built on the dream runtime.

The OpenHarness taxonomy reserves ``ohmo/`` for the persona'd, long-lived
assistant that rides on the harness core. This is dream's equivalent,
built entirely from the public SDK surface: ``dream.build_harness`` +
``dream.Runtime`` + custom research tools + a persona. It demonstrates
the Model A shape — the SDK is the mechanism, the agent (persona, tools,
workspace conventions) is policy living outside ``src/dream``.

Run it::

    DREAM_API_KEY=... DREAM_MODEL=... python examples/ohmo/agent.py \
        --workspace ~/ohmo-lab --wake-idle-minutes 30

Steer it from another terminal::

    python -m dream.ctl --working-dir ~/ohmo-lab submit "research mamba-2"
    python -m dream.ctl --working-dir ~/ohmo-lab status
    python -m dream.ctl --working-dir ~/ohmo-lab events --last 20
"""
