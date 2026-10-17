-- The munition's own recent path: the last <=10 ECEF samples, flat
-- [x0,y0,z0, x1,y1,z1, ...], oldest first, the last triple equal to
-- ecef_x/y/z. NULL on rows written before this migration, or by a projector
-- that predates it. Written by the projector (ADR-0049).
ALTER TABLE "public"."munition_track" ADD COLUMN "trail_ecef" double precision[] NULL;
