-- Create "link_status" table
CREATE TABLE "public"."link_status" (
  "id" text NOT NULL,
  "link_state" text NOT NULL,
  "traffic" text NOT NULL,
  "declared_idle" boolean NOT NULL DEFAULT false,
  "heartbeat_age_s" double precision NULL,
  "last_heartbeat_at" timestamptz NULL,
  "bridge_lag" bigint NOT NULL DEFAULT -1,
  "updated_at" timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY ("id"),
  CONSTRAINT "link_status_link_state_check" CHECK (link_state IN ('up', 'idle', 'down', 'unknown')),
  CONSTRAINT "link_status_traffic_check" CHECK (traffic IN ('active', 'idle', 'unspecified'))
);
