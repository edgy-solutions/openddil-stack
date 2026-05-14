-- Create "edge_buffer_status" table
CREATE TABLE "public"."edge_buffer_status" (
  "id" text NOT NULL,
  "bridge_group_lag" bigint NOT NULL DEFAULT 0,
  "hq_link_severed" boolean NOT NULL DEFAULT false,
  "probe_healthy" boolean NOT NULL DEFAULT false,
  "updated_at" timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY ("id")
);
