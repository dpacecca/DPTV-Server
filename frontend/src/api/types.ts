export type ChannelType = "live" | "vod" | "series";
export type SourceType = "xtream" | "m3u";
export type EpgMatchType = "none" | "auto" | "manual";
export type DummyEpgMode = "inherit" | "off" | "name" | "event";

export interface Source {
  id: number;
  name: string;
  type: SourceType;
  base_url: string | null;
  username: string | null;
  password: string | null;
  m3u_url: string | null;
  m3u_uses_userpass: boolean;
  prefix: string;
  suffix: string;
  color: string;
  ignore_vod: boolean;
  ignore_series: boolean;
  auto_sync_on_start: boolean;
  auto_enable_new_groups: boolean;
  auto_clear_removed_days: number | null;
  provider_uses_tokens: boolean;
  use_api_for_series: boolean;
  enabled: boolean;
  last_sync_at: string | null;
  last_sync_status: string | null;
  last_sync_error: string | null;
}

export interface SourceCategory {
  id: number;
  external_id: string;
  name: string;
  channel_type: ChannelType;
  enabled: boolean;
  channel_count: number;
}

export interface SourceChannel {
  id: number;
  name: string;
  external_stream_id: string;
  stream_type: ChannelType;
  tvg_id: string | null;
  logo_url: string | null;
  removed_at: string | null;
}

export interface PaginatedSourceChannels {
  items: SourceChannel[];
  total: number;
  offset: number;
  limit: number;
}

export interface EpgSource {
  id: number;
  name: string;
  url: string;
  refresh_interval_minutes: number;
  last_refreshed_at: string | null;
  last_refresh_status: string | null;
  last_refresh_error: string | null;
  channel_count: number;
}

export interface PlaylistChannel {
  id: number;
  name: string;
  name_locked: boolean;
  number: number | null;
  enabled: boolean;
  sort_order: number;
  logo_url: string | null;
  manual_stream_url: string | null;
  provider_name: string | null;
  source_channel_id: number | null;
  epg_channel_id: number | null;
  epg_display_name: string | null;
  epg_match_type: EpgMatchType;
  dummy_epg_mode: DummyEpgMode;
  dummy_epg_program_minutes: number | null;
}

/** Category shape used everywhere in the UI: counts only, never the (potentially huge) channel list. */
export interface PlaylistCategory {
  id: number;
  name: string;
  channel_type: ChannelType;
  sort_order: number;
  dummy_epg_for_unassigned: boolean;
  dummy_epg_program_minutes: number;
  channel_count: number;
}

export interface Playlist {
  id: number;
  name: string;
  enabled: boolean;
  xc_enabled: boolean;
  m3u_output_enabled: boolean;
  m3u_filename: string;
  epg_output_enabled: boolean;
  epg_filename: string;
  epg_days_to_keep: number | null;
  category_count: number;
  channel_count: number;
  categories?: PlaylistCategory[];
}

export interface PaginatedChannels {
  items: PlaylistChannel[];
  total: number;
  offset: number;
  limit: number;
}

export interface XcUserPlaylistLink {
  playlist_id: number;
  playlist_name: string;
  enabled: boolean;
}

export interface XcUser {
  id: number;
  username: string;
  password: string;
  enabled: boolean;
  max_connections: number;
  expiry_date: string | null;
  notes: string | null;
  playlists: XcUserPlaylistLink[];
}

export interface SyncSchedule {
  id: number;
  label: string;
  time_of_day: string;
  enabled: boolean;
  sync_sources: boolean;
  sync_epg: boolean;
}

export interface SyncRun {
  id: number;
  started_at: string;
  finished_at: string | null;
  trigger: string;
  status: string;
  summary: Record<string, unknown>;
}
