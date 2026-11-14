import { useState } from "react";
import { ActionIcon, Badge, Button, Group, Paper, Select, Stack, Table, Text, Title, Tooltip } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconPin, IconPinnedOff, IconPlayerPlay, IconTrash } from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useDebounce } from "use-debounce";
import { api } from "../api/client";

interface Version {
  channel_id: number;
  name: string;
  playlist: string | null;
  origin: "self" | "auto" | "manual";
  excluded: boolean;
  active: boolean;
  ok: boolean | null;
  reason: string | null;
  height: number | null;
  fps: number | null;
  silent: boolean | null;
  pass_streak: number;
  checked_at: number | null;
  /** (1.0.16) Other playlists with this same stream: one version, checked once. */
  also_in: string[];
}
interface VersionsResponse {
  channel: { id: number; name: string; active_id: number; pinned: boolean; checking: boolean };
  versions: Version[];
  swaps: { at: number; from: string | number | null; to: string | number | null; reason: string }[];
}

const when = (t: number | null) => (t ? new Date(t * 1000).toLocaleString() : "never");

function Verdict({ v }: { v: Version }) {
  if (v.excluded) return <Badge color="gray" variant="light">removed</Badge>;
  if (v.ok === null) return <Badge color="gray" variant="light">not checked</Badge>;
  return (
    <Tooltip label={`Checked ${when(v.checked_at)} · passed ${v.pass_streak} in a row`}>
      <Badge color={v.ok ? (v.silent ? "yellow" : "green") : "red"} variant="light">
        {v.reason}
      </Badge>
    </Tooltip>
  );
}

/**
 * (1.0.15) A channel's versions from its playlist's backup playlists: which one plays, what the last
 * check found in each (black, frozen or silent picture, stalls), pin, add, remove and check now.
 */
export default function ChannelVersionsPanel({ channelId, playlistId }: { channelId: number; playlistId: string }) {
  const qc = useQueryClient();
  const key = ["channel-versions", channelId];
  const { data } = useQuery<VersionsResponse>({
    queryKey: key,
    queryFn: () => api.get(`/api/versions/channels/${channelId}`).then((r) => r.data),
    refetchInterval: (q) => (q.state.data?.channel.checking ? 4000 : false),
  });
  const [search, setSearch] = useState("");
  const [debounced] = useDebounce(search, 300);
  const { data: candidates } = useQuery<{ channel_id: number; name: string; playlist: string }[]>({
    queryKey: ["version-candidates", channelId, debounced],
    queryFn: () => api.get(`/api/versions/channels/${channelId}/candidates`, { params: { q: debounced } }).then((r) => r.data),
  });
  const refresh = () => {
    qc.invalidateQueries({ queryKey: key });
    qc.invalidateQueries({ queryKey: ["versions-summary", playlistId] });
  };
  const failed = (err: unknown) =>
    notifications.show({
      message: (err as { response?: { data?: { detail?: string } } }).response?.data?.detail || "That didn't work",
      color: "red",
    });
  const pin = useMutation({
    mutationFn: (id: number | null) => api.put(`/api/versions/channels/${channelId}/pin`, { candidate_channel_id: id }),
    onSuccess: refresh,
    onError: failed,
  });
  const remove = useMutation({
    mutationFn: (id: number) => api.delete(`/api/versions/channels/${channelId}/versions/${id}`),
    onSuccess: refresh,
    onError: failed,
  });
  const add = useMutation({
    mutationFn: (id: number) => api.post(`/api/versions/channels/${channelId}/versions`, { candidate_channel_id: id }),
    onSuccess: () => { setSearch(""); refresh(); },
    onError: failed,
  });
  const check = useMutation({
    mutationFn: () => api.post(`/api/versions/channels/${channelId}/check`),
    onSuccess: () => {
      notifications.show({ message: "Checking every version, one at a time. This takes about 20 s each.", color: "blue" });
      refresh();
    },
    onError: failed,
  });

  const versions = data?.versions ?? [];
  const hasBackups = versions.some((v) => v.origin !== "self");
  const taken = new Set(versions.map((v) => v.channel_id));

  return (
    <Paper withBorder p="sm">
      <Stack gap="xs">
        <Group justify="space-between">
          <Title order={5}>Versions</Title>
          <Group gap="xs">
            {data?.channel.pinned && (
              <Button size="xs" variant="subtle" leftSection={<IconPinnedOff size={14} />} onClick={() => pin.mutate(null)}>
                Unpin
              </Button>
            )}
            <Tooltip label="Plays a sample of each version now, even if someone is watching on this line" multiline w={260}>
              <Button size="xs" variant="light" disabled={!hasBackups} loading={check.isPending || data?.channel.checking} onClick={() => check.mutate()}>
                Check now
              </Button>
            </Tooltip>
          </Group>
        </Group>
        {!hasBackups ? (
          <Text size="sm" c="dimmed">
            No other versions. Make a playlist with copies of this channel and set it as a backup for this playlist (Playlists
            page, "Backup for"), or add one below.
          </Text>
        ) : (
          <Table>
            <Table.Tbody>
              {versions.map((v) => (
                <Table.Tr key={v.channel_id} style={{ opacity: v.excluded ? 0.5 : 1 }}>
                  <Table.Td>
                    <Group gap={6} wrap="nowrap">
                      {v.active && (
                        <Tooltip label={data?.channel.pinned ? "Plays now (pinned)" : "Plays now"}>
                          {data?.channel.pinned ? <IconPin size={14} /> : <IconPlayerPlay size={14} />}
                        </Tooltip>
                      )}
                      <div>
                        <Text size="sm" fw={v.active ? 700 : 400}>{v.name}</Text>
                        <Text size="xs" c="dimmed">
                          {v.origin === "self" ? "this channel" : `${v.playlist ?? ""}${v.origin === "manual" ? " · added by hand" : ""}`}
                          {v.also_in?.length ? ` · also in ${v.also_in.join(", ")} (same stream)` : ""}
                        </Text>
                      </div>
                    </Group>
                  </Table.Td>
                  <Table.Td><Verdict v={v} /></Table.Td>
                  <Table.Td w={70}>
                    <Group gap={2} wrap="nowrap">
                      {!v.excluded && !(v.active && data?.channel.pinned) && (
                        <Tooltip label="Pin: always play this version">
                          <ActionIcon variant="subtle" onClick={() => pin.mutate(v.channel_id)}><IconPin size={16} /></ActionIcon>
                        </Tooltip>
                      )}
                      {v.origin !== "self" && !v.excluded && (
                        <Tooltip label="Remove: not a version of this channel">
                          <ActionIcon variant="subtle" color="red" onClick={() => remove.mutate(v.channel_id)}><IconTrash size={16} /></ActionIcon>
                        </Tooltip>
                      )}
                      {v.excluded && (
                        <Button size="compact-xs" variant="subtle" onClick={() => add.mutate(v.channel_id)}>Restore</Button>
                      )}
                    </Group>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        )}
        <Select
          size="xs"
          searchable
          placeholder="Add a version from a backup playlist..."
          searchValue={search}
          onSearchChange={setSearch}
          value={null}
          nothingFoundMessage={debounced ? "No match in the backup playlists" : "No backup playlist for this playlist"}
          data={(candidates ?? []).filter((c) => !taken.has(c.channel_id)).map((c) => ({ value: String(c.channel_id), label: `${c.name} · ${c.playlist}` }))}
          onChange={(v) => v && add.mutate(Number(v))}
        />
        {data && data.swaps.length > 0 && (
          <Stack gap={2}>
            <Text size="xs" fw={700} c="dimmed">HISTORY</Text>
            {data.swaps.slice(0, 6).map((s, i) => (
              <Text key={i} size="xs" c="dimmed">
                {when(s.at)}: {String(s.from)} → <b>{String(s.to)}</b>. {s.reason}
              </Text>
            ))}
          </Stack>
        )}
      </Stack>
    </Paper>
  );
}
