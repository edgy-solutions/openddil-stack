-- Record platform_variant on asset_registry.
--
-- The registry already records the one place every other asset-bearing
-- consumer agrees an asset's edge/region assignment lives (ADR-0028). A
-- variant is the same kind of fact: something the warfighter system or an
-- observing producer states about an asset, not something OpenDDIL derives.
-- Today a consumer that wants an asset's variant has to fall back to
-- whatever telemetry happened to carry it most recently; recording it here
-- gives logistics-fusion a stable value to prefer once the registry has
-- one.
--
-- NOT NULL DEFAULT '' rather than nullable text, matching
-- asset_element_telemetry's platform_variant column: "no variant recorded
-- yet" and "" read the same way everywhere this is consumed, so there is
-- no separate NULL case for callers to handle.
--
-- asset_registry is still absent from schema.hcl -- created by
-- 20260604202813, never added to the declarative file, and 20260906000000
-- already declined to repair that drift in an unrelated change, for the
-- same reason this migration does: mixing a schema correction into an
-- additive column change makes both harder to revert. Not repaired here
-- either.

BEGIN;

ALTER TABLE "asset_registry"
    ADD COLUMN IF NOT EXISTS "platform_variant" text NOT NULL DEFAULT '';

COMMIT;
