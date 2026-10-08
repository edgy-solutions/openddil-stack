-- egress_delivered_events -- admit a third outcome, 'failed'.
--
-- WHAT A 'failed' ROW MEANS. The forwarder exhausted its retries sending a
-- dedupe-route record to the destination, and then the 'held' write for
-- that record also failed. Rather than leave no trace, it records the
-- record as 'failed' and commits the offset. The row is the durable
-- statement that this (route, event_id) was not delivered.
--
-- A 'failed' row never blocks a later send: only 'delivered' short-circuits
-- a record as a duplicate. It may later be upserted to 'delivered' when the
-- same event_id is eventually sent; the reverse never happens -- a
-- 'delivered' row is never downgraded to 'failed' (enforced at the one
-- place that writes the row, see forwarder.py's DeliveredStore).
ALTER TABLE "public"."egress_delivered_events"
  DROP CONSTRAINT "egress_delivered_events_outcome_check",
  ADD CONSTRAINT "egress_delivered_events_outcome_check" CHECK ("outcome" IN ('held', 'delivered', 'failed'));
