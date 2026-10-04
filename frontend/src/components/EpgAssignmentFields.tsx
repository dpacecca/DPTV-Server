import { Select, Stack, Text, TextInput } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { EpgSource, Source, SourceType } from "../api/types";

export type EpgMode = "default" | "new" | "existing" | "none";

export interface EpgAssignmentValue {
  mode: EpgMode;
  epg_source_id: number | null;
  new_epg_name: string;
  new_epg_url: string;
}

export function emptyEpgAssignment(): EpgAssignmentValue {
  return { mode: "none", epg_source_id: null, new_epg_name: "", new_epg_url: "" };
}

/** Starting point for editing an existing source - "default"/"new" aren't distinguishable after
 * the fact (both just end up as epg_source_id pointing at some EpgSource row), so a source
 * that already has one assigned starts on "existing" with that EpgSource pre-selected; picking
 * "default"/"new" again from there creates a fresh EpgSource, same as at creation time. */
export function epgAssignmentFromSource(source: Source): EpgAssignmentValue {
  if (source.epg_source_id) {
    return { mode: "existing", epg_source_id: source.epg_source_id, new_epg_name: "", new_epg_url: "" };
  }
  return emptyEpgAssignment();
}

/** What to merge into a create (POST /api/sources) or update (PUT /api/sources/:id) payload. */
export function epgAssignmentToPayload(
  v: EpgAssignmentValue,
): { epg_source_id: number | null; new_epg_source: { name: string; url: string } | null } {
  if (v.mode === "existing") {
    return { epg_source_id: v.epg_source_id, new_epg_source: null };
  }
  if (v.mode === "default" || v.mode === "new") {
    return { epg_source_id: null, new_epg_source: { name: v.new_epg_name, url: v.new_epg_url } };
  }
  return { epg_source_id: null, new_epg_source: null };
}

/**
 * Lets an admin choose how a source's channels get EPG data: the provider's own advertised
 * guide (detected from the connection fields below, via POST /api/sources/detect-epg), a freshly
 * added EPG link, an already-configured one, or none at all (manual per-channel mapping stays
 * available either way). Shared between SourcesPage's "Add Source" modal and SourceDetailPage's
 * "EPG Settings" modal rather than duplicated, since the control is identical in both places.
 */
export function EpgAssignmentFields({
  value,
  onChange,
  sourceName,
  sourceType,
  baseUrl,
  username,
  password,
  m3uUrl,
}: {
  value: EpgAssignmentValue;
  onChange: (v: EpgAssignmentValue) => void;
  sourceName: string;
  sourceType: SourceType;
  baseUrl: string;
  username: string;
  password: string;
  m3uUrl: string;
}) {
  const canDetect = sourceType === "xtream" ? !!(baseUrl && username && password) : !!m3uUrl;

  const { data: detected, isFetching: detecting } = useQuery<{ url: string | null }>({
    queryKey: ["detect-epg", sourceType, baseUrl, username, password, m3uUrl],
    queryFn: () =>
      api
        .post("/api/sources/detect-epg", {
          type: sourceType,
          base_url: baseUrl || null,
          username: username || null,
          password: password || null,
          m3u_url: m3uUrl || null,
        })
        .then((r) => r.data),
    enabled: canDetect,
    staleTime: 10_000,
  });
  const detectedUrl = canDetect ? (detected?.url ?? null) : null;

  const { data: epgSources } = useQuery<EpgSource[]>({
    queryKey: ["epg-sources"],
    queryFn: () => api.get("/api/epg-sources").then((r) => r.data),
  });

  function selectMode(mode: EpgMode) {
    if (mode === "default" && detectedUrl) {
      onChange({
        mode,
        epg_source_id: null,
        new_epg_name: value.new_epg_name || `${sourceName || "Source"} EPG`,
        new_epg_url: detectedUrl,
      });
    } else {
      onChange({ ...value, mode });
    }
  }

  return (
    <Stack gap="xs">
      <Select
        label="EPG"
        data={[
          { value: "none", label: "No EPG - I'll map channels manually" },
          { value: "default", label: "Use the source's default EPG", disabled: !detectedUrl },
          { value: "new", label: "Add a new EPG link" },
          { value: "existing", label: "Select an existing EPG source" },
        ]}
        value={value.mode}
        onChange={(v) => selectMode((v as EpgMode) ?? "none")}
      />
      {value.mode === "default" && (
        <Text size="xs" c="dimmed">
          {detecting
            ? "Checking for a default EPG..."
            : detectedUrl
              ? `Detected: ${detectedUrl}`
              : "No default EPG detected yet - fill in the connection details above first."}
        </Text>
      )}
      {(value.mode === "default" || value.mode === "new") && (
        <>
          <TextInput
            label="EPG name"
            value={value.new_epg_name}
            onChange={(e) => onChange({ ...value, new_epg_name: e.currentTarget.value })}
          />
          <TextInput
            label="EPG URL"
            value={value.new_epg_url}
            onChange={(e) => onChange({ ...value, new_epg_url: e.currentTarget.value })}
            disabled={value.mode === "default"}
          />
        </>
      )}
      {value.mode === "existing" && (
        <Select
          label="Existing EPG source"
          placeholder="Choose one"
          data={(epgSources ?? []).map((e) => ({ value: String(e.id), label: e.name }))}
          value={value.epg_source_id ? String(value.epg_source_id) : null}
          onChange={(v) => onChange({ ...value, epg_source_id: v ? Number(v) : null })}
        />
      )}
    </Stack>
  );
}
