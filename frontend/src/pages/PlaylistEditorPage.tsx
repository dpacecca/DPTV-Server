import { useEffect, useRef, useState, type CSSProperties, type Dispatch, type SetStateAction } from "react";
import {
  ActionIcon,
  Badge,
  Box,
  Button,
  Checkbox,
  Group,
  Loader,
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
  IconArrowDown,
  IconArrowRight,
  IconArrowUp,
  IconClock,
  IconCopy,
  IconDots,
  IconDownload,
  IconEdit,
  IconLock,
  IconLockOpen,
  IconPlus,
  IconSearch,
  IconTrash,
  IconVideo,
  IconWand,
} from "@tabler/icons-react";
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useNavigate, useParams } from "react-router-dom";
import { useDebounce } from "use-debounce";
import { api } from "../api/client";
import type {
  ChannelType,
  DummyEpgMode,
  EpgSource,
  PaginatedChannels,
  Playlist,
  PlaylistCategory,
  PlaylistChannel,
  Source,
  SourceCategory,
} from "../api/types";
import { EmptyState } from "../App";

const CHANNEL_PAGE_SIZE = 200;
// Above this many selected channels, bulk actions still work (ids are just integers, cheap to
// ship) but we warn before firing - a 50k-row UPDATE/DELETE is a lot to ask of one request.
const LARGE_SELECTION_WARNING = 5000;

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
  const [detailChannel, setDetailChannel] = useState<PlaylistChannel | null>(null);
  const [manualChannelOpen, setManualChannelOpen] = useState(false);
  const [bulkEpgOpen, setBulkEpgOpen] = useState(false);
  const [scanDuplicatesOpen, setScanDuplicatesOpen] = useState(false);
  const [dummyEpgRulesOpen, setDummyEpgRulesOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [debouncedSearch] = useDebounce(search, 300);

  const categories = playlist?.categories ?? [];
  const activeCategory = categories.find((c) => c.id === selectedCategoryId) ?? categories[0] ?? null;

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["playlist", playlistId] });
    qc.invalidateQueries({ queryKey: ["playlist-channels", playlistId] });
  };

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

  function confirmIfLarge(count: number, verb: string) {
    if (count > LARGE_SELECTION_WARNING) {
      return confirm(`This will ${verb} ${count} channels in one request. Continue?`);
    }
    return true;
  }

  function runBulk(payload: { action: string; text?: string; find?: string; replace?: string }) {
    if (!confirmIfLarge(selectedChannelIds.size, "update")) return;
    bulkMutation.mutate({ channel_ids: [...selectedChannelIds], ...payload });
  }

  const selectAllMatchingMutation = useMutation({
    mutationFn: () =>
      api
        .get(`/api/playlists/${playlistId}/categories/${activeCategory?.id}/channels/ids`, {
          params: { q: debouncedSearch || undefined },
        })
        .then((r) => r.data.ids as number[]),
    onSuccess: (ids) => setSelectedChannelIds(new Set(ids)),
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
          <Button leftSection={<IconClock size={16} />} variant="light" onClick={() => setDummyEpgRulesOpen(true)}>
            Dummy EPG Rules...
          </Button>
          <Button leftSection={<IconDownload size={16} />} variant="light" onClick={() => setImportOpen(true)}>
            Import from Source
          </Button>
        </Group>
      </Group>

      <Group align="stretch" wrap="nowrap" gap="sm" style={{ flex: 1, minHeight: 0, alignItems: "stretch" }}>
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
                    setSearch("");
                  }}
                >
                  <Box>
                    <Text size="sm">{c.name}</Text>
                    <Text size="xs" c="dimmed">
                      {c.channel_count} channels
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
        <Paper withBorder p="xs" style={{ flex: 1, display: "flex", flexDirection: "column", minWidth: 0, minHeight: 0 }}>
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
                  <Button
                    size="xs"
                    variant="light"
                    leftSection={<IconWand size={14} />}
                    disabled={selectedChannelIds.size === 0}
                    onClick={() => setBulkEpgOpen(true)}
                  >
                    Map EPG...
                  </Button>
                  <Button size="xs" variant="light" leftSection={<IconVideo size={14} />} onClick={() => setScanDuplicatesOpen(true)}>
                    Scan Duplicates...
                  </Button>
                  <Menu>
                    <Menu.Target>
                      <Button size="xs" variant="light" rightSection={<IconDots size={14} />} disabled={selectedChannelIds.size === 0}>
                        Bulk Edit
                      </Button>
                    </Menu.Target>
                    <Menu.Dropdown>
                      <Menu.Item onClick={() => runBulk({ action: "uppercase" })}>UPPERCASE names</Menu.Item>
                      <Menu.Item onClick={() => runBulk({ action: "sentence_case" })}>Sentence case names</Menu.Item>
                      <Menu.Item
                        onClick={() => {
                          const text = prompt("Prefix to add:");
                          if (text) runBulk({ action: "add_prefix", text });
                        }}
                      >
                        Add prefix...
                      </Menu.Item>
                      <Menu.Item
                        onClick={() => {
                          const text = prompt("Suffix to add:");
                          if (text) runBulk({ action: "add_suffix", text });
                        }}
                      >
                        Add suffix...
                      </Menu.Item>
                      <Menu.Item
                        onClick={() => {
                          const find = prompt("Find:");
                          if (find === null) return;
                          const replace = prompt("Replace with:") || "";
                          runBulk({ action: "find_replace", find, replace });
                        }}
                      >
                        Find &amp; replace...
                      </Menu.Item>
                      <Menu.Item onClick={() => runBulk({ action: "lock_name" })}>Lock names (ignore provider renames)</Menu.Item>
                      <Menu.Item onClick={() => runBulk({ action: "unlock_name" })}>Unlock names</Menu.Item>
                      <Menu.Item onClick={() => runBulk({ action: "enable" })}>Enable</Menu.Item>
                      <Menu.Item onClick={() => runBulk({ action: "disable" })}>Disable</Menu.Item>
                      <Menu.Item
                        color="red"
                        onClick={() => {
                          if (confirm(`Delete ${selectedChannelIds.size} channel(s)?`)) runBulk({ action: "delete" });
                        }}
                      >
                        Delete
                      </Menu.Item>
                    </Menu.Dropdown>
                  </Menu>
                </Group>
              </Group>

              <Group justify="space-between" mb="xs">
                <TextInput
                  size="xs"
                  placeholder="Search channels..."
                  leftSection={<IconSearch size={14} />}
                  value={search}
                  onChange={(e) => setSearch(e.currentTarget.value)}
                  w={280}
                />
                {selectedChannelIds.size > 0 && (
                  <Text size="xs" c="dimmed">
                    {selectedChannelIds.size} selected
                    {" · "}
                    <Text component="span" c="indigo" style={{ cursor: "pointer" }} onClick={() => setSelectedChannelIds(new Set())}>
                      clear
                    </Text>
                  </Text>
                )}
              </Group>

              <ChannelTable
                playlistId={playlistId!}
                category={activeCategory}
                search={debouncedSearch}
                selectedChannelIds={selectedChannelIds}
                setSelectedChannelIds={setSelectedChannelIds}
                onOpenDetail={setDetailChannel}
                onChanged={invalidate}
                onSelectAllMatching={() => selectAllMatchingMutation.mutate()}
                selectAllPending={selectAllMatchingMutation.isPending}
              />
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
                onClick={() => {
                  if (!moveMode) return;
                  if (!confirmIfLarge(selectedChannelIds.size, moveMode)) return;
                  moveCopyMutation.mutate({ mode: moveMode, targetCategoryId: c.id });
                }}
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

      {playlistId && detailChannel && (
        <ChannelDetailModal
          playlistId={playlistId}
          channel={detailChannel}
          onClose={() => setDetailChannel(null)}
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

      {playlistId && (
        <BulkEpgModal
          opened={bulkEpgOpen}
          onClose={() => {
            setBulkEpgOpen(false);
            setSelectedChannelIds(new Set());
          }}
          playlistId={playlistId}
          channelIds={[...selectedChannelIds]}
          onChanged={invalidate}
        />
      )}

      {playlistId && activeCategory && (
        <ScanDuplicatesModal
          opened={scanDuplicatesOpen}
          onClose={() => setScanDuplicatesOpen(false)}
          playlistId={playlistId}
          categoryId={activeCategory.id}
          categoryName={activeCategory.name}
          onChanged={invalidate}
        />
      )}

      {playlistId && (
        <DummyEpgRulesModal
          opened={dummyEpgRulesOpen}
          onClose={() => setDummyEpgRulesOpen(false)}
          playlistId={playlistId}
        />
      )}
    </Stack>
  );
}

const ROW_HEIGHT = 44;

// Renders a category's channels as an infinite-scrolling, virtualized table: only the rows
// actually in (or near) the viewport ever exist in the DOM, and more pages are fetched as the
// user scrolls. This is what keeps the browser tab responsive on a category with 20k+ channels
// instead of trying to mount every row at once.
function ChannelTable({
  playlistId,
  category,
  search,
  selectedChannelIds,
  setSelectedChannelIds,
  onOpenDetail,
  onChanged,
  onSelectAllMatching,
  selectAllPending,
}: {
  playlistId: string;
  category: PlaylistCategory;
  search: string;
  selectedChannelIds: Set<number>;
  setSelectedChannelIds: Dispatch<SetStateAction<Set<number>>>;
  onOpenDetail: (channel: PlaylistChannel) => void;
  onChanged: () => void;
  onSelectAllMatching: () => void;
  selectAllPending: boolean;
}) {
  const parentRef = useRef<HTMLDivElement | null>(null);

  const { data, fetchNextPage, hasNextPage, isFetchingNextPage, isLoading } = useInfiniteQuery({
    queryKey: ["playlist-channels", playlistId, category.id, search],
    queryFn: ({ pageParam }) =>
      api
        .get<PaginatedChannels>(`/api/playlists/${playlistId}/categories/${category.id}/channels`, {
          params: { q: search || undefined, offset: pageParam, limit: CHANNEL_PAGE_SIZE },
        })
        .then((r) => r.data),
    initialPageParam: 0,
    getNextPageParam: (lastPage, allPages) => {
      const loaded = allPages.reduce((sum, p) => sum + p.items.length, 0);
      return loaded < lastPage.total ? loaded : undefined;
    },
  });

  const rows = data?.pages.flatMap((p) => p.items) ?? [];
  const total = data?.pages[0]?.total ?? 0;

  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => ROW_HEIGHT,
    overscan: 12,
  });

  useEffect(() => {
    const items = virtualizer.getVirtualItems();
    const last = items[items.length - 1];
    if (!last) return;
    // Guard against ever fetching pages faster than the viewport can plausibly need them: if
    // the "visible" range already covers hundreds of rows, the scroll container isn't actually
    // bounded (a layout regression broke the flex min-height chain) and blindly trusting
    // getVirtualItems() here would runaway-fetch the entire category instead of paging it.
    const viewportLooksBounded = items.length < 100;
    if (viewportLooksBounded && last.index >= rows.length - 1 && hasNextPage && !isFetchingNextPage) {
      fetchNextPage();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [virtualizer.getVirtualItems(), hasNextPage, isFetchingNextPage, rows.length]);

  if (isLoading) {
    return (
      <Group justify="center" py="xl">
        <Loader size="sm" />
      </Group>
    );
  }

  if (rows.length === 0) {
    return <EmptyState text={search ? "No channels match your search." : "No channels in this category yet."} />;
  }

  const virtualItems = virtualizer.getVirtualItems();

  return (
    <Stack gap={4} style={{ flex: 1, minHeight: 0 }}>
      <ScrollArea viewportRef={parentRef} style={{ flex: 1, minHeight: 0 }}>
        <Table stickyHeader striped highlightOnHover layout="fixed">
          {/* Body rows are absolutely positioned (virtualized), so this header row uses the
              same flex layout + column widths as ChannelRow to keep columns aligned - a plain
              table-row header would use the table column algorithm instead and drift out of
              sync with the flex-laid-out body. */}
          <Table.Thead>
            <Table.Tr display="flex">
              <Table.Th w={30}>
                <Checkbox
                  checked={rows.length > 0 && rows.every((r) => selectedChannelIds.has(r.id)) && rows.length === total}
                  indeterminate={selectedChannelIds.size > 0 && !(rows.every((r) => selectedChannelIds.has(r.id)) && rows.length === total)}
                  onChange={(e) => setSelectedChannelIds(e.currentTarget.checked ? new Set(rows.map((r) => r.id)) : new Set())}
                />
              </Table.Th>
              <Table.Th style={{ flex: 1, minWidth: 0 }}>Name</Table.Th>
              <Table.Th w={180}>EPG</Table.Th>
              <Table.Th w={120}>Dummy EPG</Table.Th>
              <Table.Th w={90}>Enabled</Table.Th>
              <Table.Th w={40} />
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody style={{ height: virtualizer.getTotalSize(), position: "relative" }}>
            {virtualItems.map((virtualRow) => {
              const ch = rows[virtualRow.index];
              if (!ch) return null;
              return (
                <ChannelRow
                  key={ch.id}
                  channel={ch}
                  selected={selectedChannelIds.has(ch.id)}
                  style={{ position: "absolute", top: 0, left: 0, right: 0, transform: `translateY(${virtualRow.start}px)`, height: ROW_HEIGHT }}
                  onToggleSelect={() =>
                    setSelectedChannelIds((prev) => {
                      const next = new Set(prev);
                      if (next.has(ch.id)) next.delete(ch.id);
                      else next.add(ch.id);
                      return next;
                    })
                  }
                  playlistId={playlistId}
                  onOpenDetail={() => onOpenDetail(ch)}
                  onChanged={onChanged}
                />
              );
            })}
          </Table.Tbody>
        </Table>
      </ScrollArea>
      <Group justify="space-between">
        <Text size="xs" c="dimmed">
          {rows.length} of {total} loaded{isFetchingNextPage ? " · loading more..." : ""}
        </Text>
        {total > rows.length && rows.length > 0 && !(selectedChannelIds.size === total) && (
          <Button size="xs" variant="subtle" loading={selectAllPending} onClick={onSelectAllMatching}>
            Select all {total} matching
          </Button>
        )}
      </Group>
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
  style,
}: {
  channel: PlaylistChannel;
  selected: boolean;
  onToggleSelect: () => void;
  playlistId: string;
  onOpenDetail: () => void;
  onChanged: () => void;
  style?: CSSProperties;
}) {
  const toggleEnabled = useMutation({
    mutationFn: (enabled: boolean) => api.patch(`/api/playlists/${playlistId}/channels/${channel.id}`, { enabled }),
    onSuccess: onChanged,
  });

  return (
    <Table.Tr style={style} display="flex">
      <Table.Td w={30}>
        <Checkbox checked={selected} onChange={onToggleSelect} />
      </Table.Td>
      <Table.Td style={{ cursor: "pointer", flex: 1, minWidth: 0 }} onClick={onOpenDetail}>
        <Group gap={6}>
          {channel.name_locked ? <IconLock size={12} /> : null}
          <Text size="sm">{channel.name}</Text>
        </Group>
      </Table.Td>
      <Table.Td w={180} style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
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
      <Table.Td w={120}>
        <Badge variant="outline">{channel.dummy_epg_mode}</Badge>
      </Table.Td>
      <Table.Td w={90}>
        <Switch checked={channel.enabled} onChange={(e) => toggleEnabled.mutate(e.currentTarget.checked)} />
      </Table.Td>
      <Table.Td w={40}>
        <ActionIcon variant="subtle" onClick={onOpenDetail}>
          <IconEdit size={16} />
        </ActionIcon>
      </Table.Td>
    </Table.Tr>
  );
}

function ChannelDetailModal({
  playlistId,
  channel,
  onClose,
  onChanged,
}: {
  playlistId: string;
  channel: PlaylistChannel;
  onClose: () => void;
  onChanged: () => void;
}) {
  const channelId = channel.id;
  const [name, setName] = useState(channel.name);
  const [search, setSearch] = useState("");
  const [epgSourceIds, setEpgSourceIds] = useState<Set<number>>(new Set());
  const [epgSourcesInitialized, setEpgSourcesInitialized] = useState(false);
  const [suggestRulesOpen, setSuggestRulesOpen] = useState(false);

  const { data: epgSources } = useQuery<EpgSource[]>({
    queryKey: ["epg-sources-lite"],
    queryFn: () => api.get("/api/epg-sources").then((r) => r.data),
  });

  useEffect(() => {
    if (epgSources && !epgSourcesInitialized) {
      setEpgSourceIds(new Set(epgSources.map((s) => s.id)));
      setEpgSourcesInitialized(true);
    }
  }, [epgSources, epgSourcesInitialized]);

  const activeEpgSourceIds = epgSources && epgSourceIds.size === epgSources.length ? undefined : [...epgSourceIds];

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
    mutationFn: () =>
      api.post(`/api/playlists/${playlistId}/channels/${channelId}/epg/auto`, null, {
        params: { epg_source_ids: activeEpgSourceIds },
      }),
    onSuccess: (res) => {
      onChanged();
      notifications.show({
        message: res.data.matched ? `Matched: ${res.data.display_name}` : "No confident match found",
        color: res.data.matched ? "green" : "yellow",
      });
    },
  });

  const { data: searchResults } = useQuery({
    queryKey: ["epg-search", playlistId, channelId, search, activeEpgSourceIds],
    queryFn: () =>
      api
        .get(`/api/playlists/${playlistId}/channels/${channelId}/epg/search`, {
          params: { q: search || undefined, epg_source_ids: activeEpgSourceIds },
        })
        .then((r) => r.data),
  });

  const assignEpgMutation = useMutation({
    mutationFn: (epgChannelId: number | null) => api.patch(`/api/playlists/${playlistId}/channels/${channelId}/epg`, { epg_channel_id: epgChannelId }),
    onSuccess: onChanged,
  });

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
        {epgSources && epgSources.length > 1 && (
          <Group gap={4}>
            <Text size="xs" c="dimmed">
              Search:
            </Text>
            {epgSources.map((s) => (
              <Badge
                key={s.id}
                size="sm"
                variant={epgSourceIds.has(s.id) ? "filled" : "outline"}
                style={{ cursor: "pointer" }}
                onClick={() =>
                  setEpgSourceIds((prev) => {
                    const next = new Set(prev);
                    if (next.has(s.id)) next.delete(s.id);
                    else next.add(s.id);
                    return next;
                  })
                }
              >
                {s.name}
              </Badge>
            ))}
          </Group>
        )}
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
          <Stack gap={4}>
            <Text size="xs" c="dimmed">
              Looks for a date/time in the channel name (e.g. "Team A vs Team B 08/25 9:00PM") and schedules a single
              program at that time for the configured duration, with the channel name filling the rest of the day.
              Custom rules (playlist-wide) are tried first for naming conventions the built-in parser can't handle.
            </Text>
            <Button size="xs" variant="light" onClick={() => setSuggestRulesOpen(true)} style={{ alignSelf: "flex-start" }}>
              Suggest Rule from This Name...
            </Button>
          </Stack>
        )}
      </Stack>

      <DummyEpgRulesModal
        opened={suggestRulesOpen}
        onClose={() => setSuggestRulesOpen(false)}
        playlistId={playlistId}
        initialSampleName={suggestRulesOpen ? name : undefined}
      />
    </Modal>
  );
}

function BulkEpgModal({
  opened,
  onClose,
  playlistId,
  channelIds,
  onChanged,
}: {
  opened: boolean;
  onClose: () => void;
  playlistId: string;
  channelIds: number[];
  onChanged: () => void;
}) {
  const [selectedEpgSourceIds, setSelectedEpgSourceIds] = useState<Set<number>>(new Set());
  const [sensitivity, setSensitivity] = useState(0.9);
  const [result, setResult] = useState<{ matched: { channel_name: string; display_name: string }[]; unmatched: { channel_name: string }[] } | null>(null);

  const { data: epgSources } = useQuery<EpgSource[]>({
    queryKey: ["epg-sources-lite"],
    queryFn: () => api.get("/api/epg-sources").then((r) => r.data),
    enabled: opened,
  });

  // Default to "search everything" the first time sources load for this modal session.
  useEffect(() => {
    if (opened && epgSources && selectedEpgSourceIds.size === 0 && result === null) {
      setSelectedEpgSourceIds(new Set(epgSources.map((s) => s.id)));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [opened, epgSources]);

  const bulkMapMutation = useMutation({
    mutationFn: () =>
      api
        .post(`/api/playlists/${playlistId}/channels/epg/bulk-auto-map`, {
          channel_ids: channelIds,
          sensitivity,
          epg_source_ids: epgSources && selectedEpgSourceIds.size === epgSources.length ? null : [...selectedEpgSourceIds],
        })
        .then((r) => r.data),
    onSuccess: (data) => {
      setResult(data);
      onChanged();
    },
  });

  function handleClose() {
    setResult(null);
    onClose();
  }

  return (
    <Modal opened={opened} onClose={handleClose} title={`Map EPG for ${channelIds.length} channel(s)`} size="md">
      <Stack>
        <Text size="sm" fw={600}>
          Search these EPG sources
        </Text>
        <Stack gap={4}>
          {(epgSources ?? []).map((s) => (
            <Checkbox
              key={s.id}
              label={s.name}
              checked={selectedEpgSourceIds.has(s.id)}
              onChange={(e) => {
                const checked = e.currentTarget.checked;
                setSelectedEpgSourceIds((prev) => {
                  const next = new Set(prev);
                  if (checked) next.add(s.id);
                  else next.delete(s.id);
                  return next;
                });
              }}
            />
          ))}
          {epgSources?.length === 0 && (
            <Text size="sm" c="dimmed">
              No EPG sources yet — add one under EPG Sources first.
            </Text>
          )}
        </Stack>

        <NumberInput
          label="Sensitivity"
          description="Lower it if close-but-not-exact channel names aren't matching"
          value={sensitivity}
          onChange={(v) => setSensitivity(typeof v === "number" ? v : 0.9)}
          min={0.5}
          max={1}
          step={0.05}
          decimalScale={2}
        />

        {result && (
          <Stack gap={4}>
            <Text size="sm" c="green">
              Matched {result.matched.length} of {result.matched.length + result.unmatched.length}
            </Text>
            {result.unmatched.length > 0 && (
              <Text size="xs" c="dimmed">
                Not matched: {result.unmatched.map((u) => u.channel_name).join(", ")}
              </Text>
            )}
          </Stack>
        )}

        <Button
          onClick={() => bulkMapMutation.mutate()}
          loading={bulkMapMutation.isPending}
          disabled={selectedEpgSourceIds.size === 0}
        >
          Auto-map {channelIds.length} channel(s)
        </Button>
      </Stack>
    </Modal>
  );
}

interface ScanChannelResult {
  channel_id: number;
  name: string;
  status: string;
  fps: number | null;
  bitrate_kbps: number | null;
  resolution_label: string | null;
}

interface DuplicateGroup {
  key: string;
  channel_ids: number[];
  best_channel_id: number | null;
}

interface ScanJobResult {
  job_id: string;
  status: "running" | "done" | "error";
  total: number;
  completed: number;
  error: string | null;
  results: ScanChannelResult[];
  duplicate_groups: DuplicateGroup[];
}

// Scans a whole category (not just the current selection) because a duplicate pair is only
// findable if both members get probed - selecting just one of them would miss the match. The
// scan itself runs as a background job (GET .../scan-jobs/{id} polled below) rather than one
// request/response, since probing dozens of live streams over the network is too slow to fit in
// a single HTTP round trip without risking a proxy/browser timeout.
function ScanDuplicatesModal({
  opened,
  onClose,
  playlistId,
  categoryId,
  categoryName,
  onChanged,
}: {
  opened: boolean;
  onClose: () => void;
  playlistId: string;
  categoryId: number;
  categoryName: string;
  onChanged: () => void;
}) {
  const [concurrency, setConcurrency] = useState(2);
  const [jobId, setJobId] = useState<string | null>(null);
  const [keepChoice, setKeepChoice] = useState<Record<string, number>>({});
  const [tagResolution, setTagResolution] = useState(false);
  const [applySummary, setApplySummary] = useState<{ removed: number; tagged: number } | null>(null);

  const startMutation = useMutation({
    mutationFn: () =>
      api
        .post(`/api/playlists/${playlistId}/categories/${categoryId}/scan-duplicates`, { concurrency })
        .then((r) => r.data as { job_id: string; total: number }),
    onSuccess: (data) => {
      setJobId(data.job_id);
      setKeepChoice({});
      setApplySummary(null);
    },
  });

  const { data: job } = useQuery<ScanJobResult>({
    queryKey: ["scan-job", playlistId, jobId],
    queryFn: () => api.get(`/api/playlists/${playlistId}/scan-jobs/${jobId}`).then((r) => r.data),
    enabled: !!jobId,
    refetchInterval: (query) => (query.state.data?.status === "running" ? 1200 : false),
  });

  // Default each group's keep choice to the scan's own best-quality pick, once per completed job.
  useEffect(() => {
    if (job?.status === "done" && Object.keys(keepChoice).length === 0 && job.duplicate_groups.length > 0) {
      const defaults: Record<string, number> = {};
      for (const g of job.duplicate_groups) {
        if (g.best_channel_id) defaults[g.key] = g.best_channel_id;
      }
      setKeepChoice(defaults);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job?.status]);

  const resultById = new Map((job?.results ?? []).map((r) => [r.channel_id, r]));
  const failedNotInGroups = (job?.results ?? []).filter(
    (r) => r.status !== "ok" && !(job?.duplicate_groups ?? []).some((g) => g.channel_ids.includes(r.channel_id))
  );

  const applyMutation = useMutation({
    mutationFn: async () => {
      const groups = (job?.duplicate_groups ?? [])
        .filter((g) => keepChoice[g.key])
        .map((g) => ({
          keep_channel_id: keepChoice[g.key],
          remove_channel_ids: g.channel_ids.filter((id) => id !== keepChoice[g.key]),
        }))
        .filter((g) => g.remove_channel_ids.length > 0);
      const removeIds = new Set(groups.flatMap((g) => g.remove_channel_ids));

      let removed = 0;
      if (groups.length > 0) {
        const r = await api.post(`/api/playlists/${playlistId}/channels/dedupe/apply`, { groups });
        removed = r.data.removed;
      }

      let tagged = 0;
      if (tagResolution) {
        const channelIds = (job?.results ?? []).map((r) => r.channel_id).filter((id) => !removeIds.has(id));
        if (channelIds.length > 0) {
          const r = await api.post(`/api/playlists/${playlistId}/channels/tag-resolution`, { channel_ids: channelIds });
          tagged = r.data.tagged.length;
        }
      }
      return { removed, tagged };
    },
    onSuccess: (data) => {
      setApplySummary(data);
      onChanged();
    },
  });

  function handleClose() {
    setJobId(null);
    setKeepChoice({});
    setTagResolution(false);
    setApplySummary(null);
    onClose();
  }

  return (
    <Modal opened={opened} onClose={handleClose} title={`Scan "${categoryName}" for duplicates`} size="lg">
      <Stack>
        {!jobId && (
          <>
            <Text size="sm" c="dimmed">
              Probes every channel's stream (via ffprobe) to detect its real resolution,
              framerate, and bitrate, then groups channels that look like the same feed at
              different qualities (e.g. "ESPN" / "ESPN HD"). This hits every stream directly and
              can take a while for a large category — keep concurrency low if your provider caps
              concurrent connections.
            </Text>
            <NumberInput
              label="Concurrency"
              description="How many streams to probe at once"
              value={concurrency}
              onChange={(v) => setConcurrency(typeof v === "number" ? v : 2)}
              min={1}
              max={8}
            />
            <Button onClick={() => startMutation.mutate()} loading={startMutation.isPending}>
              Start Scan
            </Button>
          </>
        )}

        {jobId && job?.status === "running" && (
          <Stack align="center" py="md">
            <Loader size="sm" />
            <Text size="sm">
              Scanning... {job.completed} / {job.total}
            </Text>
          </Stack>
        )}

        {jobId && job?.status === "error" && (
          <Text size="sm" c="red">
            Scan failed: {job.error}
          </Text>
        )}

        {jobId && job?.status === "done" && !applySummary && (
          <>
            {job.duplicate_groups.length === 0 ? (
              <Text size="sm" c="dimmed">
                No duplicate channels found in this category.
              </Text>
            ) : (
              <Stack gap="md">
                <Text size="sm" fw={600}>
                  {job.duplicate_groups.length} duplicate group(s) found — pick which channel to keep in each
                </Text>
                {job.duplicate_groups.map((g) => (
                  <Paper key={g.key} withBorder p="xs">
                    <Text size="xs" fw={600} tt="capitalize" mb={4}>
                      {g.key}
                    </Text>
                    <Stack gap={2}>
                      {g.channel_ids.map((cid) => {
                        const r = resultById.get(cid);
                        return (
                          <Group key={cid} justify="space-between" wrap="nowrap">
                            <Group gap="xs" wrap="nowrap">
                              <Checkbox
                                size="xs"
                                checked={keepChoice[g.key] === cid}
                                onChange={() => setKeepChoice((prev) => ({ ...prev, [g.key]: cid }))}
                              />
                              <Text size="xs">{r?.name}</Text>
                            </Group>
                            <Text size="xs" c={r?.status === "ok" ? "dimmed" : "red"}>
                              {r?.status === "ok"
                                ? `${r.resolution_label ?? "?"} · ${r.fps ?? "?"}fps · ${r.bitrate_kbps ?? "?"}kbps`
                                : r?.status}
                            </Text>
                          </Group>
                        );
                      })}
                    </Stack>
                  </Paper>
                ))}
              </Stack>
            )}

            {failedNotInGroups.length > 0 && (
              <Text size="xs" c="dimmed">
                Couldn't probe: {failedNotInGroups.map((r) => r.name).join(", ")}
              </Text>
            )}

            <Checkbox
              label='Also tag detected resolution into channel names (e.g. "ESPN [1080p]")'
              checked={tagResolution}
              onChange={(e) => setTagResolution(e.currentTarget.checked)}
            />

            <Button
              onClick={() => applyMutation.mutate()}
              loading={applyMutation.isPending}
              disabled={Object.keys(keepChoice).length === 0 && !tagResolution}
            >
              Apply
            </Button>
          </>
        )}

        {applySummary && (
          <Stack gap={4}>
            <Text size="sm" c="green">
              Removed {applySummary.removed} duplicate channel(s)
              {tagResolution ? `, tagged ${applySummary.tagged} channel name(s)` : ""}.
            </Text>
            <Button variant="light" onClick={handleClose}>
              Done
            </Button>
          </Stack>
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
  const [importMode, setImportMode] = useState<"per_category" | "merge">("per_category");
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
        mode: importMode,
        category_ids: [...selectedSourceCategories],
        ...(importMode === "merge"
          ? {
              target_category_id: targetMode === "existing" ? Number(targetCategoryId) : null,
              target_category_name: targetMode === "new" ? newCategoryName : null,
            }
          : {}),
      }),
    onSuccess: (res) => {
      onImported();
      onClose();
      const count = importMode === "per_category" ? res.data.categories?.length ?? 0 : 1;
      const label = importMode === "per_category" ? `${count} categor${count === 1 ? "y" : "ies"}` : "1 category";
      notifications.show({ message: `Imported ${res.data.imported} channel(s) into ${label}`, color: "green" });
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
          Categories to import
          {importMode === "merge" && (
            <Text component="span" size="xs" c="dimmed" fw={400}>
              {" "}
              (leave empty to import all enabled)
            </Text>
          )}
        </Text>
        <ScrollArea h={160} style={{ border: "1px solid var(--mantine-color-default-border)", borderRadius: 6 }} p="xs">
          <Stack gap={4}>
            {relevantCategories.map((c) => (
              <Checkbox
                key={c.id}
                label={`${c.name} (${c.channel_count})`}
                checked={selectedSourceCategories.has(c.id)}
                onChange={(e) => {
                  const checked = e.currentTarget.checked;
                  setSelectedSourceCategories((prev) => {
                    const next = new Set(prev);
                    if (checked) next.add(c.id);
                    else next.delete(c.id);
                    return next;
                  });
                }}
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
          Import as
        </Text>
        <Stack gap={4}>
          <Button
            variant={importMode === "per_category" ? "filled" : "light"}
            size="xs"
            justify="flex-start"
            onClick={() => setImportMode("per_category")}
          >
            One category per selection, keeping the provider's names &amp; order
          </Button>
          <Button
            variant={importMode === "merge" ? "filled" : "light"}
            size="xs"
            justify="flex-start"
            onClick={() => setImportMode("merge")}
          >
            Merge everything into one category I choose
          </Button>
        </Stack>

        {importMode === "per_category" ? (
          <Text size="xs" c="dimmed">
            Each selected category becomes (or reuses, if a category with that name already
            exists here) its own category in this playlist, in the same relative order the
            provider lists them.
          </Text>
        ) : (
          <>
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
          </>
        )}

        <Tooltip label="New channels added by the provider later will automatically be imported here too">
          <Text size="xs" c="dimmed">
            Linked categories auto-import new channels on future syncs.
          </Text>
        </Tooltip>

        <Button
          onClick={() => importMutation.mutate()}
          loading={importMutation.isPending}
          disabled={
            !sourceId ||
            (importMode === "per_category"
              ? selectedSourceCategories.size === 0
              : targetMode === "new"
                ? !newCategoryName
                : !targetCategoryId)
          }
        >
          Import
        </Button>
      </Stack>
    </Modal>
  );
}

interface DummyEpgRule {
  id: number;
  name: string;
  pattern: string;
  timezone: string | null;
  enabled: boolean;
  sort_order: number;
}

interface DummyEpgRuleTestResult {
  matched: boolean;
  error: string | null;
  start?: string;
  title?: string;
}

// Manages the playlist-wide custom regex rules tried (in order, first match wins) when a
// channel's dummy EPG mode is "event", before falling back to the built-in month/day parser -
// for naming conventions the built-in parser doesn't handle (different date order, separators,
// or a title that needs its own capture group).
function DummyEpgRulesModal({
  opened,
  onClose,
  playlistId,
  initialSampleName,
}: {
  opened: boolean;
  onClose: () => void;
  playlistId: string;
  initialSampleName?: string;
}) {
  const qc = useQueryClient();
  const [newName, setNewName] = useState("");
  const [newPattern, setNewPattern] = useState("");
  const [newTimezone, setNewTimezone] = useState<string | null>(null);
  const [sampleName, setSampleName] = useState("");
  const [testResult, setTestResult] = useState<DummyEpgRuleTestResult | null>(null);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editName, setEditName] = useState("");
  const [editPattern, setEditPattern] = useState("");
  const [editTimezone, setEditTimezone] = useState<string | null>(null);

  const { data: timezones } = useQuery<string[]>({
    queryKey: ["timezones"],
    queryFn: () => api.get("/api/playlists/timezones").then((r) => r.data),
    enabled: opened,
    staleTime: Infinity,
  });

  const { data: rules } = useQuery<DummyEpgRule[]>({
    queryKey: ["dummy-epg-rules", playlistId],
    queryFn: () => api.get(`/api/playlists/${playlistId}/dummy-epg-rules`).then((r) => r.data),
    enabled: opened,
  });

  const invalidate = () => qc.invalidateQueries({ queryKey: ["dummy-epg-rules", playlistId] });
  const errorMessage = (err: unknown) =>
    (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail || "Request failed";

  const createMutation = useMutation({
    mutationFn: () =>
      api.post(`/api/playlists/${playlistId}/dummy-epg-rules`, {
        name: newName,
        pattern: newPattern,
        timezone: newTimezone,
      }),
    onSuccess: () => {
      invalidate();
      setNewName("");
      setNewPattern("");
      setNewTimezone(null);
      setTestResult(null);
      notifications.show({ message: "Rule added", color: "green" });
    },
    onError: (err) => notifications.show({ message: errorMessage(err), color: "red" }),
  });

  const updateMutation = useMutation({
    mutationFn: (payload: { id: number; name?: string; pattern?: string; timezone?: string | null; enabled?: boolean }) => {
      const { id, ...body } = payload;
      return api.patch(`/api/playlists/${playlistId}/dummy-epg-rules/${id}`, body);
    },
    onSuccess: () => {
      invalidate();
      setEditingId(null);
    },
    onError: (err) => notifications.show({ message: errorMessage(err), color: "red" }),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.delete(`/api/playlists/${playlistId}/dummy-epg-rules/${id}`),
    onSuccess: invalidate,
  });

  const reorderMutation = useMutation({
    mutationFn: (items: { id: number; sort_order: number }[]) =>
      api.post(`/api/playlists/${playlistId}/dummy-epg-rules/reorder`, items),
    onSuccess: invalidate,
  });

  const testMutation = useMutation({
    mutationFn: () =>
      api
        .post(`/api/playlists/${playlistId}/dummy-epg-rules/test`, {
          pattern: newPattern,
          sample_name: sampleName,
          timezone: newTimezone,
        })
        .then((r) => r.data as DummyEpgRuleTestResult),
    onSuccess: setTestResult,
  });

  const suggestMutation = useMutation({
    mutationFn: (name: string) =>
      api
        .post(`/api/playlists/${playlistId}/dummy-epg-rules/suggest`, { sample_name: name })
        .then((r) => r.data as { suggested: boolean; pattern?: string; start?: string; title?: string }),
    onSuccess: (data) => {
      if (!data.suggested || !data.pattern) {
        setTestResult({ matched: false, error: "Couldn't find a date/time in that name to build a rule from." });
        return;
      }
      setNewPattern(data.pattern);
      setTestResult({ matched: true, error: null, start: data.start, title: data.title });
    },
  });

  // Opened from a channel's "Suggest Rule..." button: pre-fill the sample name and suggest
  // immediately, so the admin lands straight on a candidate pattern for that exact channel.
  useEffect(() => {
    if (opened && initialSampleName) {
      setSampleName(initialSampleName);
      suggestMutation.mutate(initialSampleName);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [opened, initialSampleName]);

  function moveRule(index: number, direction: -1 | 1) {
    if (!rules) return;
    const target = index + direction;
    if (target < 0 || target >= rules.length) return;
    const reordered = [...rules];
    [reordered[index], reordered[target]] = [reordered[target], reordered[index]];
    reorderMutation.mutate(reordered.map((r, i) => ({ id: r.id, sort_order: i })));
  }

  function handleClose() {
    setEditingId(null);
    setTestResult(null);
    setSampleName("");
    setNewPattern("");
    setNewName("");
    setNewTimezone(null);
    onClose();
  }

  const timezoneOptions = ["", ...(timezones ?? [])].map((tz) => ({
    value: tz,
    label: tz === "" ? "UTC (default)" : tz,
  }));

  return (
    <Modal opened={opened} onClose={handleClose} title="Dummy EPG Rules" size="lg">
      <Stack>
        <Text size="xs" c="dimmed">
          When a channel's Dummy EPG mode is "Parse event date/time from name", these rules are
          tried in order (first match wins) to pull a date/time and clean title out of the
          channel name, before falling back to the built-in parser. A pattern must define named
          groups (?P&lt;hour&gt;..) and (?P&lt;minute&gt;..); optionally (?P&lt;ampm&gt;..),
          (?P&lt;month&gt;..), (?P&lt;day&gt;..), (?P&lt;year&gt;..), and (?P&lt;title&gt;..) (the
          cleaned title — if omitted, the matched portion is stripped out of the name instead).
          Since channel names never say which timezone that hour/minute is in, each rule has its
          own Timezone setting — the parsed event keeps that zone's offset all the way to the XMLTV
          output, where every player already localizes it to the viewer's own device. The lead-up
          to the event is filled with 3-hour "Up Next: &lt;event&gt; at &lt;time&gt;" blocks
          (in that same zone) instead of one generic filler.
        </Text>

        <Stack gap={4}>
          {(rules ?? []).map((rule, i) => (
            <Paper key={rule.id} withBorder p="xs">
              {editingId === rule.id ? (
                <Stack gap={6}>
                  <TextInput size="xs" label="Name" value={editName} onChange={(e) => setEditName(e.currentTarget.value)} />
                  <TextInput
                    size="xs"
                    label="Pattern"
                    value={editPattern}
                    onChange={(e) => setEditPattern(e.currentTarget.value)}
                    styles={{ input: { fontFamily: "monospace" } }}
                  />
                  <Select
                    size="xs"
                    label="Timezone"
                    description="Zone the pattern's hour/minute is expressed in"
                    data={timezoneOptions}
                    value={editTimezone ?? ""}
                    onChange={(v) => setEditTimezone(v || null)}
                    searchable
                  />
                  <Group gap="xs">
                    <Button
                      size="xs"
                      onClick={() =>
                        updateMutation.mutate({ id: rule.id, name: editName, pattern: editPattern, timezone: editTimezone })
                      }
                      loading={updateMutation.isPending}
                    >
                      Save
                    </Button>
                    <Button size="xs" variant="subtle" onClick={() => setEditingId(null)}>
                      Cancel
                    </Button>
                  </Group>
                </Stack>
              ) : (
                <Group justify="space-between" wrap="nowrap">
                  <Box style={{ minWidth: 0 }}>
                    <Group gap={6}>
                      <Text size="sm" fw={600}>
                        {rule.name}
                      </Text>
                      <Badge size="xs" variant="light">
                        {rule.timezone || "UTC"}
                      </Badge>
                      {!rule.enabled && (
                        <Badge size="xs" color="gray">
                          Disabled
                        </Badge>
                      )}
                    </Group>
                    <Text size="xs" c="dimmed" ff="monospace" style={{ wordBreak: "break-all" }}>
                      {rule.pattern}
                    </Text>
                  </Box>
                  <Group gap={4} wrap="nowrap">
                    <ActionIcon variant="subtle" size="sm" disabled={i === 0} onClick={() => moveRule(i, -1)}>
                      <IconArrowUp size={14} />
                    </ActionIcon>
                    <ActionIcon
                      variant="subtle"
                      size="sm"
                      disabled={i === (rules?.length ?? 0) - 1}
                      onClick={() => moveRule(i, 1)}
                    >
                      <IconArrowDown size={14} />
                    </ActionIcon>
                    <Switch
                      size="xs"
                      checked={rule.enabled}
                      onChange={(e) => updateMutation.mutate({ id: rule.id, enabled: e.currentTarget.checked })}
                    />
                    <ActionIcon
                      variant="subtle"
                      size="sm"
                      onClick={() => {
                        setEditingId(rule.id);
                        setEditName(rule.name);
                        setEditPattern(rule.pattern);
                        setEditTimezone(rule.timezone);
                      }}
                    >
                      <IconEdit size={14} />
                    </ActionIcon>
                    <ActionIcon
                      variant="subtle"
                      color="red"
                      size="sm"
                      onClick={() => {
                        if (confirm(`Delete rule "${rule.name}"?`)) deleteMutation.mutate(rule.id);
                      }}
                    >
                      <IconTrash size={14} />
                    </ActionIcon>
                  </Group>
                </Group>
              )}
            </Paper>
          ))}
          {rules?.length === 0 && (
            <Text size="sm" c="dimmed">
              No custom rules yet — "event" mode uses the built-in month/day parser.
            </Text>
          )}
        </Stack>

        <Text size="sm" fw={600} mt="sm">
          Add a rule
        </Text>
        <TextInput
          size="xs"
          label="Name"
          placeholder="e.g. DD-MM sports format"
          value={newName}
          onChange={(e) => setNewName(e.currentTarget.value)}
        />
        <TextInput
          size="xs"
          label="Pattern"
          placeholder="(?P<title>.+?)\s+(?P<day>\d{1,2})-(?P<month>\d{1,2})\s+(?P<hour>\d{1,2}):(?P<minute>\d{2})"
          value={newPattern}
          onChange={(e) => setNewPattern(e.currentTarget.value)}
          styles={{ input: { fontFamily: "monospace" } }}
        />
        <Select
          size="xs"
          label="Timezone"
          description="Zone the pattern's hour/minute is expressed in - channel names never say, so this is how the parser knows"
          data={timezoneOptions}
          value={newTimezone ?? ""}
          onChange={(v) => setNewTimezone(v || null)}
          searchable
        />

        <Group align="flex-end" gap="xs">
          <TextInput
            size="xs"
            label="Test against a sample channel name"
            placeholder="Real Madrid vs Barcelona 25-08 21:00"
            value={sampleName}
            onChange={(e) => setSampleName(e.currentTarget.value)}
            style={{ flex: 1 }}
          />
          <Button
            size="xs"
            variant="light"
            onClick={() => suggestMutation.mutate(sampleName)}
            loading={suggestMutation.isPending}
            disabled={!sampleName}
          >
            Suggest
          </Button>
          <Button
            size="xs"
            variant="light"
            onClick={() => testMutation.mutate()}
            loading={testMutation.isPending}
            disabled={!newPattern || !sampleName}
          >
            Test
          </Button>
        </Group>
        {testResult &&
          (testResult.matched ? (
            <Text size="xs" c="green">
              Matched — title: "{testResult.title}", start:{" "}
              {testResult.start ? new Date(testResult.start).toLocaleString() : "?"}
            </Text>
          ) : (
            <Text size="xs" c={testResult.error ? "red" : "orange"}>
              {testResult.error ?? "No match against this sample name."}
            </Text>
          ))}

        <Button onClick={() => createMutation.mutate()} loading={createMutation.isPending} disabled={!newName || !newPattern}>
          Add Rule
        </Button>
      </Stack>
    </Modal>
  );
}

