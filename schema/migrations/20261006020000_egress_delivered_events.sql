-- egress_delivered_events -- a durable record of what the forwarder has
-- already sent (or deliberately withheld) to a destination, consulted
-- before every send on a route that sets dedupe_field.
--
-- WHY THIS EXISTS. Every Restate wipe (each upgrade) and every scenario
-- reset re-emits CM revisions, and a revision reuses its event_id, so the
-- forwarder can see the same event_id again across restarts even though
-- the destination already has it. This table lets the forwarder answer
-- "have I already sent this" itself, rather than depending on the
-- destination's own idempotency to avoid a double send.
--
-- PRIMARY KEY (route, event_id): a route's own notion of "have I sent
-- this" is scoped to that route -- two routes forwarding the same
-- event_id to two different destinations are two separate facts, not one.
--
-- outcome is 'held' or 'delivered'. A held row is a durable record of what
-- was withheld -- not a terminal fact, and never blocks a later send: the
-- same (route, event_id) row is upserted to 'delivered' once the route is
-- un-held and the record is actually sent. The reverse never happens --
-- once delivered, never downgraded back to held -- enforced at the one
-- place that writes the row (see forwarder.py's DeliveredStore).
--
-- http_status/case_id are only ever set for a 'delivered' row: the
-- destination's own response to the send this row records. A rejected
-- (non-retryable 4xx) send is never recorded here at all -- this table is
-- "what went out", not "everything attempted".
CREATE TABLE "public"."egress_delivered_events" (
  "route"             text NOT NULL,
  "event_id"          text NOT NULL,
  "outcome"           text NOT NULL,
  "http_status"       int NULL,
  "case_id"           text NULL,
  "topic"             text NOT NULL,
  "partition"         int NOT NULL,
  "kafka_offset"      bigint NOT NULL,
  "first_recorded_at" timestamptz NOT NULL,
  "recorded_at"       timestamptz NOT NULL,
  PRIMARY KEY ("route", "event_id"),
  CONSTRAINT "egress_delivered_events_outcome_check" CHECK ("outcome" IN ('held', 'delivered'))
);
