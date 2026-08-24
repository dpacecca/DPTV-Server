import { useMemo, useState } from "react";
import {
  ActionIcon,
  Badge,
  Box,
  Button,
  Checkbox,
  Group,
  Menu,
  Modal,
  NumberInput,
  Paper,
  ScrollArea,
  Select,
  Stack,
  Switch,
  Table,
  Text,
  TextInput,
  Title,
  Tooltip,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import {
  IconArrowRight,
  IconCopy,
  IconDots,
  IconDownload,
  IconEdit,
  IconLock,
  IconLockOpen,
  IconPlus,
  IconTrash,
  IconWand,
} from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useParams } from "react-router-dom";
import { api } from "../api/client";
import type { ChannelType, DummyEpgMode, Playlist, PlaylistCategory, PlaylistChannel, Source, SourceCategory } from "../api/types";
import { EmptyState } from "../App";

function useApiPlaylist(playlistId: string | undefined) {
  return useQuery<Playlist>({
    queryKey: ["playlist", playlistId],
    queryFn: () => api.get(`/api/playlists/${playlistId}`).then((r) => r.data),
    enabled: !!playlistId,
  });
}

export default function PlaylistEditorPage() {
  const { playlistId } = useParams();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { data: playlist, isLoading } = useApiPlaylist(playlistId);

  const [selectedCategoryId, setSelectedCategoryId] = useState<number | null>(null);
  const [selectedChannelIds, setSelectedChannelIds] = useState<Set<number>>(new Set());
  const [newCategoryOpen, setNewCategoryOpen] = useState(false);
  const [newCategoryName, setNewCategoryName] = useState("");
  const [importOpen, setImportOpen] = useState(false);
  const [moveMode, setMoveMode] = useState<"move" | "copy" | null>(null);
  const [detailChannelId, setDetailChannelId] = useState<number | null>(null);
  const [manualChannelOpen, setManualChannelOpen] = useState(false);

  const categories = playlist?.categories ?? [];
  const activeCategory = categories.find((c) => c.id === selectedCategoryId) ?? categories[0] ?? null;

  const invalidate = () => qc.invalidateQueries({ queryKey: ["playlist", playlistId] });

  const createCategoryMutation = useMutation({
    mutationFn: (name: string) => api.post(`/api/playlists/${playlistId}/categories`, { name, channel_type: "live" }),
    onSuccess: () => {
      invalidate();
      setNewCategoryOpen(false);
      setNewCategoryName("");
    },
  });

  const deleteCategoryMutation = useMutation({
    mutationFn: (id: number) => api.delete(`/api/playlists/${playlistId}/categories/${id}`),
    onSuccess: () => {
      invalidate();
      setSelectedCategoryId(null);
    },
  });

  const bulkMutation = useMutation({
    mutationFn: (payload: { channel_ids: number[]; action: string; text?: string; find?: string; replace?: string }) =>
      api.post(`/api/playlists/${playlistId}/channels/bulk`, payload),
    onSuccess: () => {
      invalidate();
      setSelectedChannelIds(new Set());
    },
  });

  const moveCopyMutation = useMutation({
    mutationFn: ({ mode, targetCategoryId }: { mode: "move" | "copy"; targetCategoryId: number }) =>
      api.post(`/api/playlists/${playlistId}/channels/${mode}`, {
        channel_ids: [...selectedChannelIds],
        target_category_id: targetCategoryId,
      }),
    onSuccess: () => {
      invalidate();
      setSelectedChannelIds(new Set());
      setMoveMode(null);
      notifications.show({ message: "Done", color: "green" });
    },
  });

  if (isLoading) return <Text>Loading...</Text>;
  if (!playlist) return <Text>Playlist not found</Text>;

  return (
    <Stack gap="sm" h="calc(100vh - 100px)">
      <Group justify="space-between">
        <Group>
          <Button variant="subtle" size="xs" onClick={() => navigate("/playlists")}>
            &larr; Playlists
          </Button>
          <Title order={3}>{playlist.name}</Title>
        </Group>
        <Group>
          <Button leftSection={<IconDownload size={16} />} variant="light" onClick={() => setImportOpen(true)}>
            Import from Source
          </Button>
        </Group>
      </Group>

      <Group align="stretch" gap="sm" style={{ flex: 1, minHeight: 0 }}>
        {/* Category list */}
        <Paper withBorder p="xs" w={260} style={{ display: "flex", flexDirection: "column" }}>
          <Group justify="space-between" mb="xs">
            <Text fw={600} size="sm">
              Categories
            </Text>
            <ActionIcon variant="subtle" onClick={() => setNewCategoryOpen(true)}>
              <IconPlus size={16} />
            </ActionIcon>
          </Group>
          <ScrollArea style={{ flex: 1 }}>
            <Stack gap={2}>
              {categories.map((c) => (
                <Group
                  key={c.id}
                  justify="space-between"
                  p={6}
                  style={{
                    borderRadius: 6,
                    cursor: "pointer",
                    background: activeCategory?.id === c.id ? "var(--mantine-color-indigo-light)" : undefined,
                  }}
                  onClick={() => {
                    setSelectedCategoryId(c.id);
                    setSelectedChannelIds(new Set());
                  }}
                >
                  <Box>
                    <Text size="sm">{c.name}</Text>
                    <Text size="xs" c="dimmed">
                      {c.channels.length} channels
                    </Text>
                  </Box>
                  <ActionIcon
                    variant="subtle"
                    color="red"
                    size="sm"
                    onClick={(e) => {
                      e.stopPropagation();
                      if (confirm(`Delete category "${c.name}"?`)) deleteCategoryMutation.mutate(c.id);
                    }}
                  >
                    <IconTrash size={14} />
                  </ActionIcon>
                </Group>
              ))}
              {categories.length === 0 && (
                <Text c="dimmed" size="sm" p="sm">
                  No categories yet. Add one, or import from a source (which creates categories automatically).
                </Text>
              )}
            </Stack>
          </ScrollArea>
        </Paper>

        {/* Channel list */}
        <Paper withBorder p="xs" style={{ flex: 1, display: "flex", flexDirection: "column", minWidth: 0 }}>
          {!activeCategory ? (
            <EmptyState text="Select or create a category to manage its channels" />
          ) : (
            <>
              <Group justify="space-between" mb="xs">
                <Text fw={600} size="sm">
                  {activeCategory.name}
                </Text>
                <Group gap="xs">
                  <Button size="xs" variant="light" leftSection={<IconPlus size={14} />} onClick={() => setManualChannelOpen(true)}>
                    Add Channel
                  </Button>
                  <Button
                    size="xs"
                    variant="light"
                    leftSection={<IconArrowRight size={14} />}
                    disabled={selectedChannelIds.size === 0}
                    onClick={() => setMoveMode("move")}
                  >
                    Move to...
                  </Button>
                  <Button
                    size="xs"
                    variant="light"
                    leftSection={<IconCopy size={14} />}
                    disabled={selectedChannelIds.size === 0}
                    onClick={() => setMoveMode("copy")}
                  >
                    Copy to...
                  </Button>
                  <Menu>
                    <Menu.Target>
                      <Button size="xs" variant="light" rightSection={<IconDots size={14} />} disabled={selectedChannelIds.size === 0}>
                        Bulk Edit
                      </Button>
                    </Menu.Target>
                    <Menu.Dropdown>
                      <Menu.Item onClick={() => bulkMutation.mutate({ channel_ids: [...selectedChannelIds], action: "uppercase" })}>
                        UPPERCASE names
                      </Menu.Item>
                      <Menu.Item onClick={() => bulkMutation.mutate({ channel_ids: [...selectedChannelIds], action: "sentence_case" })}>
                        Sentence case names
                      </Menu.Item>
                      <Menu.Item
                        onClick={() => {
                          const text = prompt("Prefix to add:");
                          if (text) bulkMutation.mutate({ channel_ids: [...selectedChannelIds], action: "add_prefix", text });
                        }}
                      >
                        Add prefix...
                      </Menu.Item>
                      <Menu.Item
                        onClick={() => {
                          const text = prompt("Suffix to add:");
                          if (text) bulkMutation.mutate({ channel_ids: [...selectedChannelIds], action: "add_suffix", text });
                        }}
                      >
                        Add suffix...
                      </Menu.Item>
                      <Menu.Item
                        onClick={() => {
                          const find = prompt("Find:");
                          if (find === null) return;
                          const replace = prompt("Replace with:") || "";
                          bulkMutation.mutate({ channel_ids: [...selectedChannelIds], action: "find_replace", find, replace });
                        }}
                      >
                        Find &amp; replace...
                      </Menu.Item>
                      <Menu.Item onClick={() => bulkMutation.mutate({ channel_ids: [...selectedChannelIds], action: "lock_name" })}>
                        Lock names (ignore provider renames)
                      </Menu.Item>
                      <Menu.Item onClick={() => bulkMutation.mutate({ channel_ids: [...selectedChannelIds], action: "unlock_name" })}>
                        Unlock names
                      </Menu.Item>
                      <Menu.Item onClick={() => bulkMutation.mutate({ channel_ids: [...selectedChannelIds], action: "enable" })}>
                        Enable
                      </Menu.Item>
                      <Menu.Item onClick={() => bulkMutation.mutate({ channel_ids: [...selectedChannelIds], action: "disable" })}>
                        Disable
                      </Menu.Item>
                      <Menu.Item
                        color="red"
                        onClick={() => {
                          if (confirm(`Delete ${selectedChannelIds.size} channel(s)?`))
                            bulkMutation.mutate({ channel_ids: [...selectedChannelIds], action: "delete" });
                        }}
                      >
                        Delete
                      </Menu.Item>
                    </Menu.Dropdown>
                  </Menu>
                </Group>
              </Group>

              <ScrollArea style={{ flex: 1 }}>
                <Table stickyHeader striped highlightOnHover>
                  <Table.Thead>
                    <Table.Tr>
                      <Table.Th style={{ width: 30 }}>
                        <Checkbox
                          checked={activeCategory.channels.length > 0 && selectedChannelIds.size === activeCategory.channels.length}
                          indeterminate={selectedChannelIds.size > 0 && selectedChannelIds.size < activeCategory.channels.length}
                          onChange={(e) =>
                            setSelectedChannelIds(e.currentTarget.checked ? new Set(activeCategory.channels.map((c) => c.id)) : new Set())
                          }
                        />
                      </Table.Th>
                      <Table.Th>Name</Table.Th>
                      <Table.Th>EPG</Table.Th>
                      <Table.Th>Dummy EPG</Table.Th>
                      <Table.Th>Enabled</Table.Th>
                      <Table.Th />
                    </Table.Tr>
                  </Table.Thead>
                  <Table.Tbody>
                    {activeCategory.channels.map((ch) => (
                      <ChannelRow
                        key={ch.id}
                        channel={ch}
                        selected={selectedChannelIds.has(ch.id)}
                        onToggleSelect={() =>
                          setSelectedChannelIds((prev) => {
                            const next = new Set(prev);
                            if (next.has(ch.id)) next.delete(ch.id);
                            else next.add(ch.id);
                            return next;
                          })
                        }
                        playlistId={playlistId!}
                        onOpenDetail={() => setDetailChannelId(ch.id)}
                        onChanged={invalidate}
                      />
                    ))}
                  </Table.Tbody>
                </Table>
                {activeCategory.channels.length === 0 && <EmptyState text="No channels in this category yet." />}
              </ScrollArea>
            </>
          )}
        </Paper>
      </Group>

      <Modal opened={newCategoryOpen} onClose={() => setNewCategoryOpen(false)} title="New Category">
        <Stack>
          <TextInput label="Name" value={newCategoryName} onChange={(e) => setNewCategoryName(e.currentTarget.value)} />
          <Button onClick={() => createCategoryMutation.mutate(newCategoryName)} disabled={!newCategoryName}>
            Create
          </Button>
        </Stack>
      </Modal>

      <Modal opened={moveMode !== null} onClose={() => setMoveMode(null)} title={moveMode === "move" ? "Move to..." : "Copy to..."}>
        <Stack>
          <Text size="sm" c="dimmed">
            {selectedChannelIds.size} channel(s) selected
          </Text>
          {categories
            .filter((c) => c.id !== activeCategory?.id)
            .map((c) => (
              <Button
                key={c.id}
                variant="light"
                justify="space-between"
                onClick={() => moveMode && moveCopyMutation.mutate({ mode: moveMode, targetCategoryId: c.id })}
              >
                {c.name}
              </Button>
            ))}
        </Stack>
      </Modal>

      {playlistId && (
        <ImportModal
          opened={importOpen}
          onClose={() => setImportOpen(false)}
          playlistId={playlistId}
          categories={categories}
          onImported={invalidate}
        />
      )}

      {playlistId && detailChannelId && (
        <ChannelDetailModal
          playlistId={playlistId}
          channelId={detailChannelId}
          onClose={() => setDetailChannelId(null)}
          onChanged={invalidate}
        />
      )}

      {playlistId && activeCategory && (
        <ManualChannelModal
          opened={manualChannelOpen}
          onClose={() => setManualChannelOpen(false)}
          playlistId={playlistId}
          categoryId={activeCategory.id}
          onCreated={invalidate}
        />
      )}
    </Stack>
  );
}

function ChannelRow({
  channel,
  selected,
  onToggleSelect,
  playlistId,
  onOpenDetail,
  onChanged,
}: {
  channel: PlaylistChannel;
  selected: boolean;
  onToggleSelect: () => void;
  playlistId: string;
  onOpenDetail: () => void;
  onChanged: () => void;
}) {
  const toggleEnabled = useMutation({
    mutationFn: (enabled: boolean) => api.patch(`/api/playlists/${playlistId}/channels/${channel.id}`, { enabled }),
    onSuccess: onChanged,
  });

  return (
    <Table.Tr>
      <Table.Td>
        <Checkbox checked={selected} onChange={onToggleSelect} />
      </Table.Td>
      <Table.Td style={{ cursor: "pointer" }} onClick={onOpenDetail}>
        <Group gap={6}>
          {channel.name_locked ? <IconLock size={12} /> : null}
          <Text size="sm">{channel.name}</Text>
        </Group>
      </Table.Td>
      <Table.Td>
        {channel.epg_channel_id ? (
          <Badge color={channel.epg_match_type === "manual" ? "blue" : "teal"} variant="light">
            {channel.epg_display_name}
          </Badge>
        ) : (
          <Badge color="gray" variant="light">
            unmapped
          </Badge>
        )}
      </Table.Td>
      <Table.Td>
        <Badge variant="outline">{channel.dummy_epg_mode}</Badge>
      </Table.Td>
      <Table.Td>
        <Switch checked={channel.enabled} onChange={(e) => toggleEnabled.mutate(e.currentTarget.checked)} />
      </Table.Td>
      <Table.Td>
        <ActionIcon variant="subtle" onClick={onOpenDetail}>
          <IconEdit size={16} />
        </ActionIcon>
      </Table.Td>
    </Table.Tr>
  );
}

function ChannelDetailModal({
  playlistId,
  channelId,
  onClose,
  onChanged,
}: {
  playlistId: string;
  channelId: number;
  onClose: () => void;
  onChanged: () => void;
}) {
  const { data: playlist } = useApiPlaylist(playlistId);
  const channel = useMemo(
    () => playlist?.categories?.flatMap((c) => c.channels).find((c) => c.id === channelId) ?? null,
    [playlist, channelId]
  );

  const [name, setName] = useState(channel?.name ?? "");
  const [search, setSearch] = useState("");

  const updateMutation = useMutation({
    mutationFn: (payload: Partial<PlaylistChannel>) => api.patch(`/api/playlists/${playlistId}/channels/${channelId}`, payload),
    onSuccess: onChanged,
  });

  const revertMutation = useMutation({
    mutationFn: () => api.post(`/api/playlists/${playlistId}/channels/${channelId}/revert-name`),
    onSuccess: (res) => {
      setName(res.data.name);
      onChanged();
    },
  });

  const autoEpgMutation = useMutation({
    mutationFn: () => api.post(`/api/playlists/${playlistId}/channels/${channelId}/epg/auto`),
    onSuccess: (res) => {
      onChanged();
      notifications.show({
        message: res.data.matched ? `Matched: ${res.data.display_name}` : "No confident match found",
        color: res.data.matched ? "green" : "yellow",
      });
    },
  });

  const { data: searchResults } = useQuery({
    queryKey: ["epg-search", playlistId, channelId, search],
    queryFn: () =>
      api
        .get(`/api/playlists/${playlistId}/channels/${channelId}/epg/search`, { params: { q: search || undefined } })
        .then((r) => r.data),
  });

  const assignEpgMutation = useMutation({
    mutationFn: (epgChannelId: number | null) => api.patch(`/api/playlists/${playlistId}/channels/${channelId}/epg`, { epg_channel_id: epgChannelId }),
    onSuccess: onChanged,
  });

  if (!channel) return null;

  return (
    <Modal opened onClose={onClose} title="Channel Settings" size="lg">
      <Stack>
        <Group align="flex-end">
          <TextInput label="Channel Name" value={name} onChange={(e) => setName(e.currentTarget.value)} style={{ flex: 1 }} />
          <Button variant="light" onClick={() => updateMutation.mutate({ name })}>
            Save
          </Button>
        </Group>
        {channel.provider_name && channel.provider_name !== name && (
          <Text size="xs" c="dimmed">
            Provider name: {channel.provider_name}{" "}
            <Text component="span" c="indigo" style={{ cursor: "pointer" }} onClick={() => revertMutation.mutate()}>
              (revert)
            </Text>
          </Text>
        )}
        <Group>
          <Switch
            label="Ignore name changes from provider"
            checked={channel.name_locked}
            onChange={(e) => updateMutation.mutate({ name_locked: e.currentTarget.checked })}
          />
          {channel.name_locked ? <IconLock size={16} /> : <IconLockOpen size={16} />}
        </Group>
        <NumberInput
          label="Channel number"
          value={channel.number ?? ""}
          onChange={(v) => updateMutation.mutate({ number: v === "" ? null : Number(v) })}
        />

        <Text fw={600} size="sm" mt="sm">
          EPG Mapping
        </Text>
        <Group>
          <Button size="xs" leftSection={<IconWand size={14} />} variant="light" onClick={() => autoEpgMutation.mutate()}>
            Auto-map
          </Button>
          {channel.epg_channel_id && (
            <Button size="xs" variant="subtle" color="red" onClick={() => assignEpgMutation.mutate(null)}>
              Clear mapping
            </Button>
          )}
        </Group>
        <TextInput placeholder="Search EPG channels..." value={search} onChange={(e) => setSearch(e.currentTarget.value)} />
        <Stack gap={4} mah={180} style={{ overflowY: "auto" }}>
          {searchResults?.map((r: { epg_channel_id: number; display_name: string; epg_id: string; score: number }) => (
            <Group
              key={r.epg_channel_id}
              justify="space-between"
              p={6}
              style={{
                borderRadius: 6,
                cursor: "pointer",
                background: channel.epg_channel_id === r.epg_channel_id ? "var(--mantine-color-indigo-light)" : undefined,
              }}
              onClick={() => assignEpgMutation.mutate(r.epg_channel_id)}
            >
              <Text size="sm">{r.display_name}</Text>
              <Badge size="xs" variant="light">
                {(r.score * 100).toFixed(0)}%
              </Badge>
            </Group>
          ))}
        </Stack>

        <Text fw={600} size="sm" mt="sm">
          Dummy EPG (used when no real guide data is mapped)
        </Text>
        <Group grow>
          <Select
            label="Mode"
            data={[
              { value: "inherit", label: "Inherit from category" },
              { value: "off", label: "Off" },
              { value: "name", label: "Channel name as program" },
              { value: "event", label: "Parse event date/time from name" },
            ]}
            value={channel.dummy_epg_mode}
            onChange={(v) => updateMutation.mutate({ dummy_epg_mode: (v as DummyEpgMode) ?? "inherit" })}
          />
          <NumberInput
            label="Program length (minutes)"
            value={channel.dummy_epg_program_minutes ?? ""}
            onChange={(v) => updateMutation.mutate({ dummy_epg_program_minutes: v === "" ? null : Number(v) })}
            min={5}
          />
        </Group>
        {channel.dummy_epg_mode === "event" && (
          <Text size="xs" c="dimmed">
            Looks for a date/time in the channel name (e.g. "Team A vs Team B 08/25 9:00PM") and schedules a single
            program at that time for the configured duration, with the channel name filling the rest of the day.
          </Text>
        )}
      </Stack>
    </Modal>
  );
}

function ManualChannelModal({
  opened,
  onClose,
  playlistId,
  categoryId,
  onCreated,
}: {
  opened: boolean;
  onClose: () => void;
  playlistId: string;
  categoryId: number;
  onCreated: () => void;
}) {
  const [name, setName] = useState("");
  const [streamUrl, setStreamUrl] = useState("");

  const createMutation = useMutation({
    mutationFn: () => api.post(`/api/playlists/${playlistId}/categories/${categoryId}/channels`, { name, stream_url: streamUrl || null }),
    onSuccess: () => {
      onCreated();
      onClose();
      setName("");
      setStreamUrl("");
    },
  });

  return (
    <Modal opened={opened} onClose={onClose} title="Add Channel">
      <Stack>
        <TextInput label="Channel Name" value={name} onChange={(e) => setName(e.currentTarget.value)} required />
        <TextInput label="Stream URL" value={streamUrl} onChange={(e) => setStreamUrl(e.currentTarget.value)} placeholder="http://..." />
        <Button onClick={() => createMutation.mutate()} disabled={!name}>
          Add
        </Button>
      </Stack>
    </Modal>
  );
}

function ImportModal({
  opened,
  onClose,
  playlistId,
  categories,
  onImported,
}: {
  opened: boolean;
  onClose: () => void;
  playlistId: string;
  categories: PlaylistCategory[];
  onImported: () => void;
}) {
  const [sourceId, setSourceId] = useState<string | null>(null);
  const [channelType, setChannelType] = useState<ChannelType>("live");
  const [selectedSourceCategories, setSelectedSourceCategories] = useState<Set<number>>(new Set());
  const [targetMode, setTargetMode] = useState<"existing" | "new">("new");
  const [targetCategoryId, setTargetCategoryId] = useState<string | null>(null);
  const [newCategoryName, setNewCategoryName] = useState("");

  const { data: sources } = useQuery<Source[]>({
    queryKey: ["sources"],
    queryFn: () => api.get("/api/sources").then((r) => r.data),
    enabled: opened,
  });

  const { data: sourceCategories } = useQuery<SourceCategory[]>({
    queryKey: ["source-categories", sourceId],
    queryFn: () => api.get(`/api/sources/${sourceId}/categories`).then((r) => r.data),
    enabled: opened && !!sourceId,
  });

  const importMutation = useMutation({
    mutationFn: () =>
      api.post(`/api/playlists/${playlistId}/import`, {
        source_id: Number(sourceId),
        channel_type: channelType,
        category_ids: [...selectedSourceCategories],
        target_category_id: targetMode === "existing" ? Number(targetCategoryId) : null,
        target_category_name: targetMode === "new" ? newCategoryName : null,
      }),
    onSuccess: (res) => {
      onImported();
      onClose();
      notifications.show({ message: `Imported ${res.data.imported} channel(s)`, color: "green" });
    },
    onError: () => notifications.show({ message: "Import failed", color: "red" }),
  });

  const relevantCategories = (sourceCategories ?? []).filter((c) => c.channel_type === channelType && c.enabled);

  return (
    <Modal opened={opened} onClose={onClose} title="Import Channels from Source" size="lg">
      <Stack>
        <Select
          label="Source"
          placeholder="Choose a source"
          data={(sources ?? []).map((s) => ({ value: String(s.id), label: s.name }))}
          value={sourceId}
          onChange={(v) => {
            setSourceId(v);
            setSelectedSourceCategories(new Set());
          }}
        />
        <Select
          label="Channel type"
          data={[
            { value: "live", label: "Live" },
            { value: "vod", label: "VOD" },
            { value: "series", label: "Series" },
          ]}
          value={channelType}
          onChange={(v) => setChannelType((v as ChannelType) ?? "live")}
        />

        <Text size="sm" fw={600}>
          Categories to import (leave empty to import all enabled)
        </Text>
        <ScrollArea h={160} style={{ border: "1px solid var(--mantine-color-default-border)", borderRadius: 6 }} p="xs">
          <Stack gap={4}>
            {relevantCategories.map((c) => (
              <Checkbox
                key={c.id}
                label={`${c.name} (${c.channel_count})`}
                checked={selectedSourceCategories.has(c.id)}
                onChange={(e) =>
                  setSelectedSourceCategories((prev) => {
                    const next = new Set(prev);
                    if (e.currentTarget.checked) next.add(c.id);
                    else next.delete(c.id);
                    return next;
                  })
                }
              />
            ))}
            {relevantCategories.length === 0 && (
              <Text size="sm" c="dimmed">
                No enabled categories of this type. Enable some on the source's detail page first.
              </Text>
            )}
          </Stack>
        </ScrollArea>

        <Text size="sm" fw={600}>
          Import into
        </Text>
        <Group>
          <Button variant={targetMode === "new" ? "filled" : "light"} size="xs" onClick={() => setTargetMode("new")}>
            New category
          </Button>
          <Button variant={targetMode === "existing" ? "filled" : "light"} size="xs" onClick={() => setTargetMode("existing")}>
            Existing category
          </Button>
        </Group>
        {targetMode === "new" ? (
          <TextInput placeholder="New category name" value={newCategoryName} onChange={(e) => setNewCategoryName(e.currentTarget.value)} />
        ) : (
          <Select
            placeholder="Choose category"
            data={categories.map((c) => ({ value: String(c.id), label: c.name }))}
            value={targetCategoryId}
            onChange={setTargetCategoryId}
          />
        )}

        <Tooltip label="New channels added by the provider later will automatically be imported here too">
          <Text size="xs" c="dimmed">
            Linked categories auto-import new channels on future syncs.
          </Text>
        </Tooltip>

        <Button
          onClick={() => importMutation.mutate()}
          loading={importMutation.isPending}
          disabled={!sourceId || (targetMode === "new" ? !newCategoryName : !targetCategoryId)}
        >
          Import
        </Button>
      </Stack>
    </Modal>
  );
}

