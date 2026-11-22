"""The declaration agent: the registry's second Faust agent (ADR-0056, DESIGN-2026-11-08 section 2.2).

Consumes `asset-declarations` (ASSET_REGISTRY_DECLARATION_TOPIC) on the registry's own
broker, ASSET_REGISTRY_KAFKA_BROKERS, in a consumer group of its own (the app id).
The chart forwards every managed edge's declarations to that broker; an unmanaged edge
writes there directly. For each record it binds or mints an identity in one
transaction (identity_store.process_declaration). It produces nothing.

A record that is not valid JSON or has no asset_id is logged and skipped inside
handle_declaration, so one bad record cannot crash-loop the agent.
"""
from __future__ import annotations

import faust

from . import identity_store


def make_declaration_app(
    *,
    brokers: str,
    topic: str,
    tier: str,
    app_id: str,
    web_port: int,
) -> faust.App:
    """A Faust App on one broker. app_id names the consumer group, so it is distinct from
    the per-edge apps' ids."""
    app = faust.App(
        app_id,
        broker=f"kafka://{brokers}",
        store="memory://",
        value_serializer="raw",
        web_port=web_port,
    )
    in_topic = app.topic(topic, value_type=bytes)

    @app.agent(in_topic)
    async def on_declaration(stream):
        async for raw in stream:
            await identity_store.handle_declaration(raw, tier=tier)

    app._declaration_agent = on_declaration  # type: ignore[attr-defined]
    return app
