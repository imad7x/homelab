-- Reporting layer for Grafana's "Media Usage" dashboard, on Jellystat's database (jfstat).
-- Functions, not views: views would pin Jellystat's columns and block its migrations.
-- Re-apply any time (idempotent):
--   docker exec -i jellystat-db psql -U jellystat -d jfstat < /docker/jellyfin/jellystat/report.sql

CREATE SCHEMA IF NOT EXISTS report;

-- One row per playback session, normalised. Sessions under 30 s are skipped (accidental clicks),
-- matching Jellystat's MINIMUM_SECONDS_TO_INCLUDE_PLAYBACK for live tracking.
-- Jellystat stores episodes as NowPlayingItemId = series id, EpisodeId = episode id.
-- Rows imported from the Playback Reporting plugin (imported = true, Id = plugin rowid) may refer to
-- titles that no longer exist; for those the plugin's ItemType / ItemName fill the gaps.
CREATE OR REPLACE FUNCTION report.plays(t_from timestamptz, t_to timestamptz)
RETURNS TABLE (
  at timestamptz, username text, kind text, title text, episode text,
  client text, device text, method text, seconds bigint, item_id text, transcode_reasons text
)
LANGUAGE sql STABLE AS $$
  SELECT
    a."ActivityDateInserted",
    a."UserName",
    CASE
      WHEN a."EpisodeId" IS NOT NULL OR coalesce(a."SeriesName", '') <> '' OR p."ItemType" = 'Episode' THEN 'TV'
      WHEN coalesce(i."Type", p."ItemType") = 'Movie' THEN 'Movies'
      WHEN coalesce(i."Type", p."ItemType") IN ('Audio', 'MusicVideo', 'MusicAlbum') THEN 'Music'
      WHEN coalesce(i."Type", p."ItemType") = 'TvChannel' THEN 'Live TV'
      ELSE coalesce(i."Type", p."ItemType", 'Other')
    END,
    -- series folders were sometimes named "Show S01"; count them as one series
    CASE WHEN a."EpisodeId" IS NOT NULL OR coalesce(a."SeriesName", '') <> ''
         THEN regexp_replace(coalesce(nullif(a."SeriesName", ''), e."SeriesName", a."NowPlayingItemName"), '\s+[sS]\d{1,2}$', '')
         ELSE a."NowPlayingItemName" END,
    CASE WHEN a."EpisodeId" IS NOT NULL OR coalesce(a."SeriesName", '') <> '' THEN
      concat_ws(' · ',
        CASE WHEN e."ParentIndexNumber" IS NOT NULL
               THEN format('S%sE%s', lpad(e."ParentIndexNumber"::text, 2, '0'), lpad(e."IndexNumber"::text, 2, '0'))
             ELSE upper((regexp_match(p."ItemName", ' - ([sS]\d+[eE]\d+) - '))[1]) END,
        coalesce(e."Name", a."NowPlayingItemName"))
    END,
    a."Client",
    a."DeviceName",
    CASE
      WHEN a."PlayMethod" ILIKE 'Transcode%'    THEN 'Transcode'
      WHEN a."PlayMethod" ILIKE 'DirectStream%' THEN 'Direct stream'
      WHEN a."PlayMethod" ILIKE 'DirectPlay%'   THEN 'Direct play'
      ELSE coalesce(nullif(a."PlayMethod", ''), 'Unknown')
    END,
    coalesce(a."PlaybackDuration", 0),
    a."NowPlayingItemId",
    (SELECT string_agg(r, ', ')
       FROM json_array_elements_text(
              CASE WHEN json_typeof(a."TranscodingInfo" -> 'TranscodeReasons') = 'array'
                   THEN a."TranscodingInfo" -> 'TranscodeReasons' ELSE '[]'::json END) AS r)
  FROM jf_playback_activity a
  LEFT JOIN jf_library_items i    ON i."Id" = a."NowPlayingItemId"
  LEFT JOIN jf_library_episodes e ON e."EpisodeId" = a."EpisodeId"
  LEFT JOIN jf_playback_reporting_plugin_data p ON a.imported AND p.rowid::text = a."Id"
  WHERE a."ActivityDateInserted" >= t_from AND a."ActivityDateInserted" < t_to
    AND coalesce(a."PlaybackDuration", 0) >= 30
$$;

-- What is playing right now (Jellystat's live session table), with progress.
CREATE OR REPLACE FUNCTION report.now_playing()
RETURNS TABLE (
  username text, title text, episode text, client text, device text,
  method text, progress numeric, paused boolean, started timestamptz
)
LANGUAGE sql STABLE AS $$
  SELECT
    w."UserName",
    coalesce(nullif(w."SeriesName", ''), w."NowPlayingItemName"),
    CASE WHEN nullif(w."SeriesName", '') IS NOT NULL THEN
      concat_ws(' · ',
        CASE WHEN e."ParentIndexNumber" IS NOT NULL
             THEN format('S%sE%s', lpad(e."ParentIndexNumber"::text, 2, '0'), lpad(e."IndexNumber"::text, 2, '0')) END,
        w."NowPlayingItemName")
    END,
    w."Client",
    w."DeviceName",
    CASE
      WHEN w."PlayMethod" ILIKE 'Transcode%'    THEN 'Transcode'
      WHEN w."PlayMethod" ILIKE 'DirectStream%' THEN 'Direct stream'
      WHEN w."PlayMethod" ILIKE 'DirectPlay%'   THEN 'Direct play'
      ELSE coalesce(nullif(w."PlayMethod", ''), 'Unknown')
    END,
    round(100.0 * ((w."PlayState" ->> 'PositionTicks')::numeric)
          / nullif(coalesce(e."RunTimeTicks", i."RunTimeTicks"), 0), 1),
    coalesce(w."IsPaused", false),
    w."ActivityDateInserted"
  FROM jf_activity_watchdog w
  LEFT JOIN jf_library_items i    ON i."Id" = w."NowPlayingItemId"
  LEFT JOIN jf_library_episodes e ON e."EpisodeId" = w."EpisodeId"
$$;

-- Library additions over time (movies, episodes, music tracks).
CREATE OR REPLACE FUNCTION report.library_added(t_from timestamptz, t_to timestamptz)
RETURNS TABLE (added timestamptz, kind text, title text)
LANGUAGE sql STABLE AS $$
  SELECT "DateCreated", 'Movies', "Name"
    FROM jf_library_items
   WHERE "Type" = 'Movie' AND NOT coalesce(archived, false)
     AND "DateCreated" >= t_from AND "DateCreated" < t_to
  UNION ALL
  SELECT "DateCreated", 'Episodes', "SeriesName" || ' · ' || "Name"
    FROM jf_library_episodes
   WHERE NOT coalesce(archived, false)
     AND "DateCreated" >= t_from AND "DateCreated" < t_to
  UNION ALL
  SELECT "DateCreated", 'Music', "Name"
    FROM jf_library_items
   WHERE "Type" = 'Audio' AND NOT coalesce(archived, false)
     AND "DateCreated" >= t_from AND "DateCreated" < t_to
$$;

GRANT USAGE ON SCHEMA report TO grafana_ro;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA report TO grafana_ro;
