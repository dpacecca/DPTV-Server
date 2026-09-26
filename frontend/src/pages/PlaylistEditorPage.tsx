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
  Popover,
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
  IconEye,
  IconGripVertical,
  IconLock,
  IconLockOpen,
  IconPlus,
  IconRefresh,
  IconSearch,
  IconSettings,
  IconTrash,
  IconTrophy,
  IconVideo,
  IconWand,
} from "@tabler/icons-react";
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useVirtualizer } from "@tanstack/react-virtual";
import { DndContext, DragOverlay, PointerSensor, closestCenter, useSensor, useSensors } from "@dnd-kit/core";
import { SortableContext, useSortable, verticalListSortingStrategy } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { useNavigate, useParams } from "react-router-dom";
import { useDebounce } from "use-debounce";
import { api, refreshSportCategory } from "../api/client";
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
  SportType,
  SupportedSport,
} from "../api/types";
import { EmptyState } from "../App";
import { toggleSelection } from "../utils/selection";

const CHANNEL_PAGE_SIZE = 200;
// Above this many selected channels, bulk actions still work (ids are just integers, cheap to
// ship) but we warn before firing - a 50k-row UPDATE/DELETE is a lot to ask of one request.
const LARGE_SELECTION_WARNING = 5000;

const EPG_SOURCE_SELECTION_STORAGE_KEY = "dptv:epgSourceSelection";

function loadStoredEpgSourceSelection(): Set<number> | null {
  try {
    const raw = sessionStorage.getItem(EPG_SOURCE_SELECTION_STORAGE_KEY);
    return raw === null ? null : new Set(JSON.parse(raw) as number[]);
  } catch {
    return null;
  }
}

// Same-tab writes never fire the native `storage` event (browsers only raise that for OTHER
// tabs/windows sharing the storage), and the single-channel modal (unmounted/remounted fresh on
// every open) sits alongside the bulk modal (mounted once for the page's lifetime, only its
// `opened` prop toggles) - so without this, picking sources in one and then opening the other
// within the same page load would show the bulk modal's stale first-mount state instead of what
// was just picked. Dispatching our own event lets every live hook instance re-sync immediately,
// regardless of which one mounted first or how long it's stayed mounted.
const EPG_SOURCE_SELECTION_EVENT = "dptv:epgSourceSelectionChanged";

function saveEpgSourceSelection(ids: Set<number>) {
  try {
    sessionStorage.setItem(EPG_SOURCE_SELECTION_STORAGE_KEY, JSON.stringify([...ids]));
    // Deferred rather than dispatched inline: this runs from inside a setState updater (see
    // setAndPersist below), and dispatching synchronously there lets the listener call setState
    // on a *different* component while React is still processing this one's update - which React
    // rejects ("Cannot update a component while rendering a different component"). Queuing it as
    // a microtask lets the current update finish committing first.
    queueMicrotask(() => window.dispatchEvent(new Event(EPG_SOURCE_SELECTION_EVENT)));
  } catch {
    // sessionStorage unavailable (private browsing, etc.) - selection just won't persist.
  }
}

// Which EPG sources to search when mapping, shared by the single-channel and bulk "Map EPG..."
// flows. Starts empty (search nothing until the admin actually picks sources) rather than
// defaulting to "all", since silently fuzzy-matching against every guide - including ones from
// completely different regions/providers - produces confident-looking wrong matches. Persisted
// to sessionStorage (not localStorage) so the choice carries over between the two modals and
// across page navigation for the rest of this browser tab's session, but doesn't linger forever
// once the tab closes.
function useEpgSourceSelection(): [Set<number>, Dispatch<SetStateAction<Set<number>>>] {
  const [ids, setIds] = useState<Set<number>>(() => loadStoredEpgSourceSelection() ?? new Set());

  useEffect(() => {
    const resync = () => setIds(loadStoredEpgSourceSelection() ?? new Set());
    window.addEventListener(EPG_SOURCE_SELECTION_EVENT, resync);
    return () => window.removeEventListener(EPG_SOURCE_SELECTION_EVENT, resync);
  }, []);

  const setAndPersist: Dispatch<SetStateAction<Set<number>>> = (action) => {
    setIds((prev) => {
      const next = typeof action === "function" ? (action as (p: Set<number>) => Set<number>)(prev) : action;
      saveEpgSourceSelection(next);
      return next;
    });
  };
  return [ids, setAndPersist];
}

// Shared drag-and-drop reorder logic for both the category sidebar and the channel table: moves
// either just the dragged item, or - when the dragged item is part of a larger multi-selection -
// every selected item as one contiguous block, to just before whatever it was dropped on.
// Preserves each moved item's relative order from before the drag.
function reorderBlock(order: number[], selectedIds: Set<number>, activeId: number, overId: number): number[] {
  if (activeId === overId) return order;
  const movingIds = selectedIds.has(activeId) && selectedIds.size > 1 ? order.filter((id) => selectedIds.has(id)) : [activeId];
  const movingSet = new Set(movingIds);
  const remaining = order.filter((id) => !movingSet.has(id));
  const insertAt = remaining.indexOf(overId);
  return insertAt === -1
    ? [...remaining, ...movingIds]
    : [...remaining.slice(0, insertAt), ...movingIds, ...remaining.slice(insertAt)];
}

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
  const [multiSelectedCategoryIds, setMultiSelectedCategoryIds] = useState<Set<number>>(new Set());
  const categorySelectionAnchorRef = useRef<number | null>(null);
  const [selectedChannelIds, setSelectedChannelIds] = useState<Set<number>>(new Set());
  const [newCategoryOpen, setNewCategoryOpen] = useState(false);
  const [newCategoryName, setNewCategoryName] = useState("");
  const [newSportCategoryOpen, setNewSportCategoryOpen] = useState(false);
  const [selectedSport, setSelectedSport] = useState<SportType | null>(null);
  const [categorySettingsTarget, setCategorySettingsTarget] = useState<PlaylistCategory | null>(null);
  const [importOpen, setImportOpen] = useState(false);
  const [moveMode, setMoveMode] = useState<"move" | "copy" | null>(null);
  const [detailChannel, setDetailChannel] = useState<PlaylistChannel | null>(null);
  const [manualChannelOpen, setManualChannelOpen] = useState(false);
  const [bulkEpgOpen, setBulkEpgOpen] = useState(false);
  const [scanDuplicatesOpen, setScanDuplicatesOpen] = useState(false);
  const [dummyEpgRulesOpen, setDummyEpgRulesOpen] = useState(false);
  const [bulkDummyEpgOpen, setBulkDummyEpgOpen] = useState(false);
  const [epgPreviewOpen, setEpgPreviewOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [debouncedSearch] = useDebounce(search, 300);
  const [draggingCategoryId, setDraggingCategoryId] = useState<number | null>(null);
  const categorySensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 4 } }));

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

  const { data: supportedSports } = useQuery<SupportedSport[]>({
    queryKey: ["sports"],
    queryFn: () => api.get("/api/playlists/sports").then((r) => r.data),
    enabled: newSportCategoryOpen,
  });

  const createSportCategoryMutation = useMutation({
    mutationFn: (sportType: SportType) => {
      const label = supportedSports?.find((s) => s.value === sportType)?.label ?? sportType;
      return api.post(`/api/playlists/${playlistId}/categories`, { name: label, channel_type: "live", sport_type: sportType });
    },
    onSuccess: () => {
      invalidate();
      setNewSportCategoryOpen(false);
      setSelectedSport(null);
    },
  });

  const sportRefreshNowMutation = useMutation({
    mutationFn: (categoryId: number) => refreshSportCategory(Number(playlistId), categoryId),
    onSuccess: () => {
      invalidate();
      notifications.show({ message: "Live sport category refreshed", color: "green" });
    },
    onError: (err: Error) => notifications.show({ message: err.message || "Refresh failed", color: "red" }),
  });

  const deleteCategoryMutation = useMutation({
    mutationFn: (id: number) => api.delete(`/api/playlists/${playlistId}/categories/${id}`),
    onSuccess: () => {
      invalidate();
      setSelectedCategoryId(null);
    },
  });

  const reorderCategoriesMutation = useMutation({
    mutationFn: (items: { id: number; sort_order: number }[]) => api.post(`/api/playlists/${playlistId}/categories/reorder`, items),
    onSuccess: invalidate,
  });

  function handleCategoryDragEnd(activeId: number, overId: number) {
    const newOrder = reorderBlock(categories.map((c) => c.id), multiSelectedCategoryIds, activeId, overId);
    reorderCategoriesMutation.mutate(newOrder.map((id, i) => ({ id, sort_order: i })));
  }

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

  // Same idea as selectAllMatchingMutation, scoped to channels with no EPG mapping at all - a
  // quick way to select exactly what still needs "Map EPG..." run against it, without
  // hand-picking rows or selecting everything and weeding out the already-mapped ones.
  const selectAllUnmappedMutation = useMutation({
    mutationFn: () =>
      api
        .get(`/api/playlists/${playlistId}/categories/${activeCategory?.id}/channels/ids`, {
          params: { q: debouncedSearch || undefined, unmapped: true },
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
            <Group gap={4}>
              <Tooltip label="Create Live Sport Category">
                <ActionIcon variant="subtle" onClick={() => setNewSportCategoryOpen(true)}>
                  <IconTrophy size={16} />
                </ActionIcon>
              </Tooltip>
              <Tooltip label="Add Category">
                <ActionIcon variant="subtle" onClick={() => setNewCategoryOpen(true)}>
                  <IconPlus size={16} />
                </ActionIcon>
              </Tooltip>
            </Group>
          </Group>
          <ScrollArea style={{ flex: 1 }}>
            <DndContext
              sensors={categorySensors}
              collisionDetection={closestCenter}
              onDragStart={(e) => setDraggingCategoryId(Number(e.active.id))}
              onDragEnd={(e) => {
                setDraggingCategoryId(null);
                const { active, over } = e;
                if (over && active.id !== over.id) handleCategoryDragEnd(Number(active.id), Number(over.id));
              }}
              onDragCancel={() => setDraggingCategoryId(null)}
            >
              <SortableContext items={categories.map((c) => c.id)} strategy={verticalListSortingStrategy}>
                <Stack gap={2}>
                  {categories.map((c) => (
                    <SortableCategoryRow
                      key={c.id}
                      category={c}
                      active={activeCategory?.id === c.id}
                      multiSelected={multiSelectedCategoryIds.has(c.id)}
                      onSelect={() => {
                        setSelectedCategoryId(c.id);
                        setSelectedChannelIds(new Set());
                        setSearch("");
                      }}
                      onToggleMultiSelect={(shiftKey) =>
                        toggleSelection(
                          c.id,
                          categories.map((cat) => cat.id),
                          shiftKey,
                          setMultiSelectedCategoryIds,
                          categorySelectionAnchorRef,
                        )
                      }
                      onOpenSettings={() => setCategorySettingsTarget(c)}
                      onDelete={() => {
                        if (confirm(`Delete category "${c.name}"?`)) deleteCategoryMutation.mutate(c.id);
                      }}
                    />
                  ))}
                  {categories.length === 0 && (
                    <Text c="dimmed" size="sm" p="sm">
                      No categories yet. Add one, or import from a source (which creates categories automatically).
                    </Text>
                  )}
                </Stack>
              </SortableContext>
              <DragOverlay>
                {draggingCategoryId != null &&
                  (multiSelectedCategoryIds.has(draggingCategoryId) && multiSelectedCategoryIds.size > 1 ? (
                    <Paper withBorder p={6} shadow="md" bg="var(--mantine-color-body)">
                      <Text size="sm">Moving {multiSelectedCategoryIds.size} categories</Text>
                    </Paper>
                  ) : (
                    <Paper withBorder p={6} shadow="md" bg="var(--mantine-color-body)">
                      <Text size="sm">{categories.find((c) => c.id === draggingCategoryId)?.name}</Text>
                    </Paper>
                  ))}
              </DragOverlay>
            </DndContext>
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
                  {activeCategory.sport_type && (
                    <>
                      <Text size="xs" c={activeCategory.sport_last_refresh_status === "failed" ? "red" : "dimmed"}>
                        {activeCategory.sport_last_refresh_status === "failed"
                          ? `Refresh failed: ${activeCategory.sport_last_refresh_error}`
                          : activeCategory.sport_last_refreshed_at
                            ? `Last refreshed ${new Date(activeCategory.sport_last_refreshed_at).toLocaleTimeString()}`
                            : "Never refreshed yet"}
                      </Text>
                      <Button
                        size="xs"
                        variant="light"
                        leftSection={<IconRefresh size={14} />}
                        loading={sportRefreshNowMutation.isPending}
                        onClick={() => sportRefreshNowMutation.mutate(activeCategory.id)}
                      >
                        Refresh Now
                      </Button>
                    </>
                  )}
                  {!activeCategory.sport_type && (
                    <>
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
                    </>
                  )}
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
                  <Button size="xs" variant="light" leftSection={<IconEye size={14} />} disabled={!activeCategory} onClick={() => setEpgPreviewOpen(true)}>
                    Preview EPG...
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
                      <Menu.Item onClick={() => setBulkDummyEpgOpen(true)}>Set Dummy EPG mode...</Menu.Item>
                      <Menu.Item
                        onClick={() => {
                          if (confirm(`Clear the EPG mapping for ${selectedChannelIds.size} channel(s)?`)) {
                            runBulk({ action: "clear_epg_mapping" });
                          }
                        }}
                      >
                        Clear EPG mapping
                      </Menu.Item>
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
                <Button
                  size="xs"
                  variant="subtle"
                  loading={selectAllUnmappedMutation.isPending}
                  onClick={() => selectAllUnmappedMutation.mutate()}
                >
                  Select all unmapped
                </Button>
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

      <Modal opened={newSportCategoryOpen} onClose={() => setNewSportCategoryOpen(false)} title="Create Live Sport Category">
        <Stack>
          <Select
            label="Sport"
            placeholder="Choose a sport"
            data={(supportedSports ?? []).map((s) => ({ value: s.value, label: s.label }))}
            value={selectedSport}
            onChange={(v) => setSelectedSport(v as SportType | null)}
          />
          <Text size="xs" c="dimmed">
            Channels showing a live match are found automatically each day and kept up to date on
            a refresh interval - there's nothing to add, remove, or reorder by hand.
          </Text>
          <Button
            onClick={() => selectedSport && createSportCategoryMutation.mutate(selectedSport)}
            disabled={!selectedSport}
            loading={createSportCategoryMutation.isPending}
          >
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
            .filter((c) => c.id !== activeCategory?.id && !c.sport_type)
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

      {playlistId && categorySettingsTarget && (
        <CategorySettingsModal
          playlistId={playlistId}
          category={categorySettingsTarget}
          onClose={() => setCategorySettingsTarget(null)}
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
        <MapEpgModal
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

      {playlistId && (
        <BulkDummyEpgModal
          opened={bulkDummyEpgOpen}
          onClose={() => {
            setBulkDummyEpgOpen(false);
            setSelectedChannelIds(new Set());
          }}
          playlistId={playlistId}
          channelIds={[...selectedChannelIds]}
          onChanged={invalidate}
        />
      )}

      {playlistId && activeCategory && (
        <EpgPreviewModal
          opened={epgPreviewOpen}
          onClose={() => setEpgPreviewOpen(false)}
          playlistId={playlistId}
          categoryId={activeCategory.id}
          categoryName={activeCategory.name}
        />
      )}

    </Stack>
  );
}

function SortableCategoryRow({
  category,
  active,
  multiSelected,
  onSelect,
  onToggleMultiSelect,
  onOpenSettings,
  onDelete,
}: {
  category: PlaylistCategory;
  active: boolean;
  multiSelected: boolean;
  onSelect: () => void;
  onToggleMultiSelect: (shiftKey: boolean) => void;
  onOpenSettings: () => void;
  onDelete: () => void;
}) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id: category.id });
  const shiftKeyRef = useRef(false);

  return (
    <Group
      ref={setNodeRef}
      justify="space-between"
      wrap="nowrap"
      p={6}
      style={{
        borderRadius: 6,
        cursor: "pointer",
        background: active ? "var(--mantine-color-indigo-light)" : multiSelected ? "var(--mantine-color-default-hover)" : undefined,
        transform: CSS.Transform.toString(transform),
        transition,
        opacity: isDragging ? 0.4 : 1,
      }}
      onClick={onSelect}
    >
      <Group gap={4} wrap="nowrap" style={{ minWidth: 0, flex: 1 }}>
        <ActionIcon variant="subtle" size="sm" style={{ cursor: "grab", touchAction: "none", flexShrink: 0 }} {...attributes} {...listeners}>
          <IconGripVertical size={14} />
        </ActionIcon>
        <Checkbox
          size="xs"
          checked={multiSelected}
          onClick={(e) => {
            e.stopPropagation();
            shiftKeyRef.current = e.shiftKey;
          }}
          onChange={() => onToggleMultiSelect(shiftKeyRef.current)}
        />
        <Box style={{ minWidth: 0 }}>
          <Group gap={4} wrap="nowrap">
            <Text size="sm" truncate>
              {category.name}
            </Text>
            {category.sport_type && (
              <Badge size="xs" variant="light" color={category.sport_last_refresh_status === "failed" ? "red" : "orange"}>
                Live
              </Badge>
            )}
          </Group>
          <Text size="xs" c="dimmed">
            {category.channel_count} {category.sport_type ? "live now" : "channels"}
          </Text>
        </Box>
      </Group>
      <Group gap={0} wrap="nowrap">
        <ActionIcon
          variant="subtle"
          size="sm"
          onClick={(e) => {
            e.stopPropagation();
            onOpenSettings();
          }}
        >
          <IconSettings size={14} />
        </ActionIcon>
        <ActionIcon
          variant="subtle"
          color="red"
          size="sm"
          onClick={(e) => {
            e.stopPropagation();
            onDelete();
          }}
        >
          <IconTrash size={14} />
        </ActionIcon>
      </Group>
    </Group>
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
  // Reordering only makes sense against the true, unfiltered sort_order - a search result is a
  // scattered subset of it, and renumbering just the visible subset would silently scramble
  // every other channel's position relative to it. See reorderMutation below for the rest of
  // the contract this relies on (the loaded window is always the lowest-sort_order prefix). A
  // Live Sport category's channel list is entirely computed by the periodic refresh job, so
  // there's no manual order to preserve there either.
  const reorderEnabled = !search && !category.sport_type;
  const channelSensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 4 } }));
  const [draggingChannelId, setDraggingChannelId] = useState<number | null>(null);
  // Shift-click range-select's anchor - only ever meaningful against rows actually loaded into
  // `rows` below, same as any other infinite-scroll list.
  const selectionAnchorRef = useRef<number | null>(null);

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
  const rowsById = new Map(rows.map((r) => [r.id, r]));

  const reorderMutation = useMutation({
    mutationFn: (items: { id: number; sort_order: number }[]) =>
      api.post(`/api/playlists/${playlistId}/categories/${category.id}/channels/reorder`, items),
    onSuccess: onChanged,
  });

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
      {!reorderEnabled && (
        <Text size="xs" c="dimmed">
          {category.sport_type ? "This category's order is set automatically." : "Clear the search to drag-reorder channels."}
        </Text>
      )}
      <DndContext
        sensors={channelSensors}
        collisionDetection={closestCenter}
        onDragStart={(e) => setDraggingChannelId(Number(e.active.id))}
        onDragEnd={(e) => {
          setDraggingChannelId(null);
          const { active, over } = e;
          if (!over || active.id === over.id) return;
          const newOrder = reorderBlock(rows.map((r) => r.id), selectedChannelIds, Number(active.id), Number(over.id));
          reorderMutation.mutate(newOrder.map((id, i) => ({ id, sort_order: i })));
        }}
        onDragCancel={() => setDraggingChannelId(null)}
      >
        <ScrollArea viewportRef={parentRef} style={{ flex: 1, minHeight: 0 }}>
          <Table stickyHeader striped highlightOnHover layout="fixed">
            {/* Body rows are absolutely positioned (virtualized), so this header row uses the
                same flex layout + column widths as ChannelRow to keep columns aligned - a plain
                table-row header would use the table column algorithm instead and drift out of
                sync with the flex-laid-out body. */}
            <Table.Thead>
              <Table.Tr display="flex">
                <Table.Th w={24} />
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
            <SortableContext items={rows.map((r) => r.id)} strategy={verticalListSortingStrategy}>
              <Table.Tbody style={{ height: virtualizer.getTotalSize(), position: "relative" }}>
                {virtualItems.map((virtualRow) => {
                  const ch = rows[virtualRow.index];
                  if (!ch) return null;
                  return (
                    <ChannelRow
                      key={ch.id}
                      channel={ch}
                      selected={selectedChannelIds.has(ch.id)}
                      reorderEnabled={reorderEnabled}
                      style={{ position: "absolute", top: virtualRow.start, left: 0, right: 0, height: ROW_HEIGHT }}
                      onToggleSelect={(shiftKey) =>
                        toggleSelection(ch.id, rows.map((r) => r.id), shiftKey, setSelectedChannelIds, selectionAnchorRef)
                      }
                      playlistId={playlistId}
                      onOpenDetail={() => onOpenDetail(ch)}
                      onChanged={onChanged}
                    />
                  );
                })}
              </Table.Tbody>
            </SortableContext>
          </Table>
        </ScrollArea>
        <DragOverlay>
          {draggingChannelId != null &&
            (selectedChannelIds.has(draggingChannelId) && selectedChannelIds.size > 1 ? (
              <Paper withBorder p={6} shadow="md" bg="var(--mantine-color-body)">
                <Text size="sm">Moving {selectedChannelIds.size} channels</Text>
              </Paper>
            ) : (
              <Paper withBorder p={6} shadow="md" bg="var(--mantine-color-body)">
                <Text size="sm">{rowsById.get(draggingChannelId)?.name}</Text>
              </Paper>
            ))}
        </DragOverlay>
      </DndContext>
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
  reorderEnabled,
  onToggleSelect,
  playlistId,
  onOpenDetail,
  onChanged,
  style,
}: {
  channel: PlaylistChannel;
  selected: boolean;
  reorderEnabled: boolean;
  onToggleSelect: (shiftKey: boolean) => void;
  playlistId: string;
  onOpenDetail: () => void;
  onChanged: () => void;
  style?: CSSProperties;
}) {
  const toggleEnabled = useMutation({
    mutationFn: (enabled: boolean) => api.patch(`/api/playlists/${playlistId}/channels/${channel.id}`, { enabled }),
    onSuccess: onChanged,
  });
  // `style.top` (a real CSS property, set by the virtualizer) is what positions every row,
  // dragged or not - dnd-kit's droppable measurement is transform-agnostic (it deliberately
  // reads each item's untransformed layout rect, so a mid-animation transform never gets
  // mistaken for a real position), so folding the virtualizer's offset into a transform instead
  // of `top` made every row measure as being at the same spot and broke collision detection
  // entirely. dnd-kit's own transform is only ever non-null for the actively dragged row (our
  // `items` array never reorders mid-drag), so stacking it on top of `top` here just makes that
  // one row visually follow the pointer; every other row is unaffected.
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id: channel.id,
    disabled: !reorderEnabled,
  });
  // Captured on click (which always fires before change) so onChange - which runs after the
  // browser's own native toggle, letting that happen normally rather than fighting it with
  // preventDefault - knows whether this was a shift-click.
  const shiftKeyRef = useRef(false);

  return (
    <Table.Tr
      ref={setNodeRef}
      style={{
        ...style,
        transform: transform ? CSS.Transform.toString({ ...transform, x: 0 }) : undefined,
        transition,
        opacity: isDragging ? 0.4 : 1,
        zIndex: isDragging ? 1 : undefined,
      }}
      display="flex"
    >
      <Table.Td w={24}>
        <Tooltip label={reorderEnabled ? "Drag to reorder" : "Clear search to reorder"} disabled={!reorderEnabled}>
          <ActionIcon
            variant="subtle"
            size="sm"
            disabled={!reorderEnabled}
            style={{ cursor: reorderEnabled ? "grab" : "not-allowed", touchAction: "none" }}
            {...attributes}
            {...listeners}
          >
            <IconGripVertical size={14} />
          </ActionIcon>
        </Tooltip>
      </Table.Td>
      <Table.Td w={30}>
        <Checkbox
          checked={selected}
          onClick={(e) => (shiftKeyRef.current = e.shiftKey)}
          onChange={() => onToggleSelect(shiftKeyRef.current)}
        />
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
  // Local, optimistically-updated mirrors of the dummy EPG fields rather than reading `channel.*`
  // directly - `channel` is a snapshot taken when this modal was opened (see `detailChannel` in
  // the parent) and is never refreshed after a mutation, so a Select/NumberInput bound straight
  // to the prop would visually snap back to its old value right after being changed, and the
  // Rule picker (gated on dummyMode === "event") would never appear even though the save itself
  // succeeded.
  const [dummyMode, setDummyMode] = useState<DummyEpgMode>(channel.dummy_epg_mode);
  const [dummyMinutes, setDummyMinutes] = useState<number | null>(channel.dummy_epg_program_minutes);
  const [dummyRuleId, setDummyRuleId] = useState<number | null>(channel.dummy_epg_rule_id);
  const [epgSourceIds, setEpgSourceIds] = useEpgSourceSelection();
  const epgSourceSelectionAnchorRef = useRef<number | null>(null);
  const epgSourceShiftKeyRef = useRef(false);
  const [suggestRulesOpen, setSuggestRulesOpen] = useState(false);
  // Which source this channel's EPG mapping section is currently showing - mirrors the bulk
  // "Map EPG..." modal's source picker (EPG source / Dummy EPG), mutually exclusive, so there's
  // one consistent place to pick a source and search/configure it instead of two permanently-
  // stacked sections. Defaults to whichever this channel is actually using right now rather than
  // always "epg", since (unlike the bulk modal) there's a real single channel state to read at
  // open time.
  const [channelMapSource, setChannelMapSource] = useState<"epg" | "dummy">(() => {
    if (channel.epg_channel_id) return "epg";
    if (channel.dummy_epg_mode !== "inherit") return "dummy";
    return "epg";
  });

  const { data: epgSources } = useQuery<EpgSource[]>({
    queryKey: ["epg-sources-lite"],
    queryFn: () => api.get("/api/epg-sources").then((r) => r.data),
  });

  const { data: dummyEpgRules } = useQuery<DummyEpgRule[]>({
    queryKey: ["dummy-epg-rules", playlistId],
    queryFn: () => api.get(`/api/playlists/${playlistId}/dummy-epg-rules`).then((r) => r.data),
    enabled: channelMapSource === "dummy",
  });

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
    // An empty epg_source_ids list means "unrestricted" server-side, not "search nothing" - so
    // with zero sources picked this has to not fire at all, rather than quietly searching every
    // guide anyway.
    enabled: epgSourceIds.size > 0,
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
        <Stack gap={4}>
          {(epgSources ?? []).map((s) => (
            <Checkbox
              key={s.id}
              label={s.name}
              checked={channelMapSource === "epg" && epgSourceIds.has(s.id)}
              disabled={channelMapSource !== "epg"}
              onClick={(e) => (epgSourceShiftKeyRef.current = e.shiftKey)}
              onChange={() => {
                setChannelMapSource("epg");
                toggleSelection(
                  s.id,
                  (epgSources ?? []).map((es) => es.id),
                  epgSourceShiftKeyRef.current,
                  setEpgSourceIds,
                  epgSourceSelectionAnchorRef,
                );
              }}
            />
          ))}
          {epgSources?.length === 0 && (
            <Text size="sm" c="dimmed">
              No EPG sources yet — add one under EPG Sources first.
            </Text>
          )}
          <Checkbox
            label="Dummy EPG"
            checked={channelMapSource === "dummy"}
            onChange={(e) => setChannelMapSource(e.currentTarget.checked ? "dummy" : "epg")}
          />
        </Stack>

        {channelMapSource === "epg" && (
          <>
            <Group>
              <Button
                size="xs"
                leftSection={<IconWand size={14} />}
                variant="light"
                onClick={() => autoEpgMutation.mutate()}
                disabled={epgSourceIds.size === 0}
              >
                Auto-map
              </Button>
              {channel.epg_channel_id && (
                <Button size="xs" variant="subtle" color="red" onClick={() => assignEpgMutation.mutate(null)}>
                  Clear mapping
                </Button>
              )}
            </Group>
            {epgSourceIds.size === 0 ? (
              <Text size="xs" c="dimmed">
                Select at least one EPG source above to search or auto-map.
              </Text>
            ) : (
              <>
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
              </>
            )}
          </>
        )}

        {channelMapSource === "dummy" && (
          <Stack gap={4}>
            <Text size="xs" c="dimmed">
              Used when no real guide data is mapped above.
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
                value={dummyMode}
                onChange={(v) => {
                  const mode = (v as DummyEpgMode) ?? "inherit";
                  setDummyMode(mode);
                  updateMutation.mutate({ dummy_epg_mode: mode });
                }}
              />
              <NumberInput
                label="Program length (minutes)"
                value={dummyMinutes ?? ""}
                onChange={(v) => {
                  const minutes = v === "" ? null : Number(v);
                  setDummyMinutes(minutes);
                  updateMutation.mutate({ dummy_epg_program_minutes: minutes });
                }}
                min={5}
              />
            </Group>
            {dummyMode === "event" && (
              <Stack gap={4}>
                <Text size="xs" c="dimmed">
                  Looks for a date/time in the channel name (e.g. "Team A vs Team B 08/25 9:00PM") and schedules a single
                  program at that time for the configured duration, with the channel name filling the rest of the day.
                  Custom rules (playlist-wide) are tried first for naming conventions the built-in parser can't handle.
                </Text>
                <Select
                  label="Rule"
                  description="Which custom rule to use - leave on the default to try every enabled rule in order."
                  data={[
                    { value: "", label: "Any enabled rule (default)" },
                    ...(dummyEpgRules ?? []).map((r) => ({
                      value: String(r.id),
                      label: r.enabled ? r.name : `${r.name} (disabled)`,
                    })),
                  ]}
                  value={dummyRuleId ? String(dummyRuleId) : ""}
                  onChange={(v) => {
                    const ruleId = v ? Number(v) : null;
                    setDummyRuleId(ruleId);
                    updateMutation.mutate({ dummy_epg_rule_id: ruleId });
                  }}
                />
                <Button size="xs" variant="light" onClick={() => setSuggestRulesOpen(true)} style={{ alignSelf: "flex-start" }}>
                  Suggest Rule from This Name...
                </Button>
              </Stack>
            )}
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

function CategorySettingsModal({
  playlistId,
  category,
  onClose,
  onChanged,
}: {
  playlistId: string;
  category: PlaylistCategory;
  onClose: () => void;
  onChanged: () => void;
}) {
  const [name, setName] = useState(category.name);
  // Local, optimistically-updated mirrors of the dummy EPG fields rather than reading
  // `category.*` directly - `category` is a snapshot taken when this modal was opened (see
  // `categorySettingsTarget` in the parent) and is never refreshed after a mutation, so a
  // Select/NumberInput bound straight to the prop would visually snap back to its old value
  // right after being changed, and the Rule picker (gated on dummyMode === "event") would never
  // appear even though the save itself succeeded.
  const [dummyMode, setDummyMode] = useState<DummyEpgMode>(
    category.dummy_epg_mode === "inherit" ? "off" : category.dummy_epg_mode,
  );
  const [dummyMinutes, setDummyMinutes] = useState(category.dummy_epg_program_minutes);
  const [dummyRuleId, setDummyRuleId] = useState<number | null>(category.dummy_epg_rule_id);

  const { data: dummyEpgRules } = useQuery<DummyEpgRule[]>({
    queryKey: ["dummy-epg-rules", playlistId],
    queryFn: () => api.get(`/api/playlists/${playlistId}/dummy-epg-rules`).then((r) => r.data),
  });

  const errorMessage = (err: unknown) =>
    (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail || "Save failed";

  const updateMutation = useMutation({
    mutationFn: (payload: Partial<PlaylistCategory>) =>
      api.patch(`/api/playlists/${playlistId}/categories/${category.id}`, payload),
    onSuccess: onChanged,
    onError: (err) => notifications.show({ message: errorMessage(err), color: "red" }),
  });

  // The Mode/Program length/Rule controls below already save themselves the instant they
  // change (same as everywhere else in this modal), so by the time this is clicked they're
  // already persisted - this only still has its own name save to do (if the name field was
  // touched), then closes the modal either way, since "Save" is the admin's one signal that
  // they're done with this dialog.
  function saveAndClose() {
    if (name !== category.name) {
      updateMutation.mutate({ name }, { onSuccess: onClose });
    } else {
      onClose();
    }
  }

  return (
    <Modal opened onClose={onClose} title="Category Settings" size="md">
      <Stack>
        <Group align="flex-end">
          <TextInput label="Name" value={name} onChange={(e) => setName(e.currentTarget.value)} style={{ flex: 1 }} />
          <Button variant="light" onClick={saveAndClose} loading={updateMutation.isPending}>
            Save
          </Button>
        </Group>

        <Text fw={600} size="sm" mt="sm">
          Default Dummy EPG for New Channels
        </Text>
        <Text size="xs" c="dimmed">
          Applies to any channel added to this category (by sync, import, or by hand) that's left
          on "Inherit" - so a newly-added channel gets correct EPG right away instead of needing
          per-channel setup. A channel with its own mode set explicitly overrides this.
        </Text>
        <Group grow>
          <Select
            label="Mode"
            data={[
              { value: "off", label: "Off" },
              { value: "name", label: "Channel name as program" },
              { value: "event", label: "Parse event date/time from name" },
            ]}
            value={dummyMode}
            onChange={(v) => {
              const mode = (v as DummyEpgMode) ?? "off";
              setDummyMode(mode);
              updateMutation.mutate({ dummy_epg_mode: mode });
            }}
          />
          <NumberInput
            label="Program length (minutes)"
            value={dummyMinutes}
            onChange={(v) => {
              const minutes = typeof v === "number" ? v : 60;
              setDummyMinutes(minutes);
              updateMutation.mutate({ dummy_epg_program_minutes: minutes });
            }}
            min={5}
          />
        </Group>
        {dummyMode === "event" && (
          <Select
            label="Rule"
            description="Which custom rule newly-added channels are pinned to - leave on the default to try every enabled rule in order."
            data={[
              { value: "", label: "Any enabled rule (default)" },
              ...(dummyEpgRules ?? []).map((r) => ({
                value: String(r.id),
                label: r.enabled ? r.name : `${r.name} (disabled)`,
              })),
            ]}
            value={dummyRuleId ? String(dummyRuleId) : ""}
            onChange={(v) => {
              const ruleId = v ? Number(v) : null;
              setDummyRuleId(ruleId);
              updateMutation.mutate({ dummy_epg_rule_id: ruleId });
            }}
          />
        )}
      </Stack>
    </Modal>
  );
}

// A single row of a bulk mapping preview.
interface MatchCandidate {
  key: number;
  label: string;
  sublabel?: string;
  score?: number;
}

interface PreviewRow {
  channel_id: number;
  channel_name: string;
  candidates: MatchCandidate[];
}

function normalizeEpgCandidate(c: { epg_channel_id: number; display_name: string; epg_id: string; score?: number }): MatchCandidate {
  return { key: c.epg_channel_id, label: c.display_name, sublabel: c.epg_id, score: c.score };
}

// One review row in the bulk mapping modal. The auto-matched top-5 candidates cover a simple
// rename (the channel's own name still resembles the target one), but not a full rebrand
// ("Fox Sports 501 HD" -> "Fox Cricket") where nothing in the original name resembles the
// correct match - so this also lets an admin type a free-text query, which re-searches
// server-side (scoped to whatever EPG sources are currently active) instead of just
// client-filtering the original 5 options.
function BulkMapRow({
  row,
  pendingValue,
  onChange,
  searchFn,
  searchKey,
}: {
  row: PreviewRow;
  pendingValue: number | null | undefined;
  onChange: (v: number | null) => void;
  searchFn: (query: string) => Promise<MatchCandidate[]>;
  searchKey: string;
}) {
  const [opened, setOpened] = useState(false);
  const [searchText, setSearchText] = useState("");
  const [debouncedSearch] = useDebounce(searchText, 300);
  const [selectedCandidate, setSelectedCandidate] = useState<MatchCandidate | null>(
    row.candidates.find((c) => c.key === pendingValue) ?? null,
  );

  const { data: searchResults, isFetching } = useQuery<MatchCandidate[]>({
    queryKey: ["bulk-map-search", row.channel_id, searchKey, debouncedSearch],
    queryFn: () => searchFn(debouncedSearch),
    enabled: opened && debouncedSearch.trim().length > 0,
  });

  const shownCandidates = debouncedSearch.trim().length > 0 ? searchResults ?? [] : row.candidates;

  function pick(c: MatchCandidate | null) {
    setSelectedCandidate(c);
    onChange(c ? c.key : null);
    setSearchText("");
    setOpened(false);
  }

  const displayValue = opened
    ? searchText
    : selectedCandidate
      ? selectedCandidate.score !== undefined
        ? `${selectedCandidate.label} — ${(selectedCandidate.score * 100).toFixed(0)}%`
        : selectedCandidate.label
      : pendingValue === null
        ? "— no mapping —"
        : "";

  return (
    <Group wrap="nowrap" gap="xs" align="center">
      <Text size="xs" style={{ flex: "0 0 40%", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
        {row.channel_name}
      </Text>
      <Popover opened={opened} onChange={setOpened} position="bottom-start" width="target" withinPortal>
        <Popover.Target>
          <TextInput
            size="xs"
            style={{ flex: 1 }}
            placeholder="Choose a match, or type to search..."
            value={displayValue}
            onFocus={() => {
              setOpened(true);
              setSearchText("");
            }}
            onChange={(e) => setSearchText(e.currentTarget.value)}
            rightSection={isFetching ? <Loader size={12} /> : undefined}
          />
        </Popover.Target>
        <Popover.Dropdown p={4}>
          <Stack gap={2} mah={220} style={{ overflowY: "auto" }}>
            <Text
              size="xs"
              c="dimmed"
              p={4}
              style={{ cursor: "pointer", borderRadius: 4 }}
              onClick={() => pick(null)}
            >
              — no mapping —
            </Text>
            {shownCandidates.map((c) => (
              <Group
                key={c.key}
                justify="space-between"
                p={4}
                wrap="nowrap"
                style={{
                  cursor: "pointer",
                  borderRadius: 4,
                  background: selectedCandidate?.key === c.key ? "var(--mantine-color-indigo-light)" : undefined,
                }}
                onClick={() => pick(c)}
              >
                <div>
                  <Text size="xs">{c.label}</Text>
                  {c.sublabel && (
                    <Text size="xs" c="dimmed">
                      {c.sublabel}
                    </Text>
                  )}
                </div>
                {c.score !== undefined && (
                  <Badge size="xs" variant="light">
                    {(c.score * 100).toFixed(0)}%
                  </Badge>
                )}
              </Group>
            ))}
            {shownCandidates.length === 0 && (
              <Text size="xs" c="dimmed" p={4}>
                {debouncedSearch.trim().length > 0 ? "No matches" : "No candidates found"}
              </Text>
            )}
          </Stack>
        </Popover.Dropdown>
      </Popover>
    </Group>
  );
}

// "Map EPG" flow: pick which EPG sources to search, preview matches, review/override any of
// them via BulkMapRow's dropdown, then Apply.
function MapEpgModal({
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
  const [selectedEpgSourceIds, setSelectedEpgSourceIds] = useEpgSourceSelection();
  const epgSourceSelectionAnchorRef = useRef<number | null>(null);
  const epgSourceShiftKeyRef = useRef(false);
  const [sensitivity, setSensitivity] = useState(0.9);
  const [preview, setPreview] = useState<{ matched: PreviewRow[]; unmatched: PreviewRow[] } | null>(null);
  // channel_id -> chosen candidate key (null = explicitly left unmapped). A row with no entry
  // here hasn't been touched and is left alone on Apply - lets a partial review (only fixing the
  // ones that look wrong) still Apply safely without clobbering the rest.
  const [pending, setPending] = useState<Record<number, number | null>>({});

  const { data: epgSources } = useQuery<EpgSource[]>({
    queryKey: ["epg-sources-lite"],
    queryFn: () => api.get("/api/epg-sources").then((r) => r.data),
    enabled: opened,
  });

  const activeEpgSourceIds = epgSources && selectedEpgSourceIds.size === epgSources.length ? null : [...selectedEpgSourceIds];

  const previewMutation = useMutation({
    mutationFn: async (): Promise<{ matched: PreviewRow[]; unmatched: PreviewRow[] }> => {
      const { data } = await api.post(`/api/playlists/${playlistId}/channels/epg/bulk-preview`, {
        channel_ids: channelIds,
        sensitivity,
        epg_source_ids: activeEpgSourceIds,
      });
      return {
        matched: data.matched.map((r: any) => ({ ...r, candidates: r.candidates.map(normalizeEpgCandidate) })),
        unmatched: data.unmatched.map((r: any) => ({ ...r, candidates: r.candidates.map(normalizeEpgCandidate) })),
      };
    },
    onSuccess: (data) => {
      setPreview(data);
      // Every previewed channel gets an explicit decision, not just the confidently-matched
      // ones - a channel that already had a mapping (e.g. from a different EPG source) but
      // doesn't confidently match this search is staged to have that mapping CLEARED, not left
      // untouched. Otherwise re-running this against a different source silently kept the old
      // source's mapping in place for anything that didn't happen to also match the new one,
      // which defeats the point of "reassign to a different source." An admin can still
      // hand-pick a different match (or restore "no mapping" on an already-cleared row) via its
      // dropdown before hitting Apply.
      const initial: Record<number, number | null> = {};
      for (const row of data.matched) {
        initial[row.channel_id] = row.candidates[0]?.key ?? null;
      }
      for (const row of data.unmatched) {
        initial[row.channel_id] = null;
      }
      setPending(initial);
    },
    onError: (err: any) =>
      notifications.show({
        message: err?.response?.data?.detail || "Preview failed - check the browser console for details",
        color: "red",
      }),
  });

  const applyMutation = useMutation({
    mutationFn: async () => {
      const entries = Object.entries(pending);
      const { data } = await api.post(`/api/playlists/${playlistId}/channels/epg/bulk-assign`, {
        assignments: entries.map(([channelId, key]) => ({ channel_id: Number(channelId), epg_channel_id: key })),
      });
      return data as { applied: number; invalid: { channel_id: number; reason: string }[] };
    },
    onSuccess: (data) => {
      onChanged();
      notifications.show({
        message: data.invalid.length
          ? `Applied ${data.applied} mapping(s), ${data.invalid.length} failed`
          : `Applied ${data.applied} mapping(s)`,
        color: data.invalid.length ? "yellow" : "green",
      });
    },
    onError: (err: any) =>
      notifications.show({
        message: err?.response?.data?.detail || "Apply failed - check the browser console for details",
        color: "red",
      }),
  });

  function resetPreview() {
    setPreview(null);
    setPending({});
  }

  function handleClose() {
    resetPreview();
    onClose();
  }

  // Scopes the per-row search-as-you-type cache to whatever sources are currently active, so
  // switching sources doesn't show stale results cached under the same row.
  const searchKey = `epg:${(activeEpgSourceIds ?? []).slice().sort().join(",")}`;

  function makeSearchFn(rowChannelId: number) {
    return async (query: string): Promise<MatchCandidate[]> => {
      const { data } = await api.get(`/api/playlists/${playlistId}/channels/${rowChannelId}/epg/search`, {
        params: { q: query, epg_source_ids: activeEpgSourceIds ?? undefined },
      });
      return data.map(normalizeEpgCandidate);
    };
  }

  const totalPending = Object.keys(pending).length;

  return (
    <Modal opened={opened} onClose={handleClose} title={`Map EPG for ${channelIds.length} channel(s)`} size="lg">
      <Stack>
        <Stack gap={4}>
          <Text size="sm" fw={600}>
            Search these EPG sources
          </Text>
          {(epgSources ?? []).map((s) => (
            <Checkbox
              key={s.id}
              label={s.name}
              checked={selectedEpgSourceIds.has(s.id)}
              onClick={(e) => (epgSourceShiftKeyRef.current = e.shiftKey)}
              onChange={() =>
                toggleSelection(
                  s.id,
                  (epgSources ?? []).map((es) => es.id),
                  epgSourceShiftKeyRef.current,
                  setSelectedEpgSourceIds,
                  epgSourceSelectionAnchorRef,
                )
              }
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

        {!preview && (
          <Stack gap={4}>
            <Button
              onClick={() => previewMutation.mutate()}
              loading={previewMutation.isPending}
              disabled={selectedEpgSourceIds.size === 0}
            >
              Preview matches for {channelIds.length} channel(s)
            </Button>
            {selectedEpgSourceIds.size === 0 && (
              <Text size="xs" c="dimmed">
                Select at least one EPG source above first.
              </Text>
            )}
          </Stack>
        )}

        {preview && (
          <Stack gap={4}>
            <Text size="sm" c="green">
              {preview.matched.length} of {preview.matched.length + preview.unmatched.length} confidently matched -
              review below and adjust any that look wrong, then Apply.
              {preview.unmatched.length > 0 && " The rest will have their EPG mapping cleared unless you pick one by hand."}
            </Text>
            <Stack gap={6} mah={320} style={{ overflowY: "auto" }}>
              {[...preview.matched, ...preview.unmatched].map((row) => (
                <BulkMapRow
                  key={row.channel_id}
                  row={row}
                  pendingValue={pending[row.channel_id]}
                  onChange={(v) => setPending((prev) => ({ ...prev, [row.channel_id]: v }))}
                  searchFn={makeSearchFn(row.channel_id)}
                  searchKey={searchKey}
                />
              ))}
            </Stack>

            <Group>
              <Button onClick={() => applyMutation.mutate()} loading={applyMutation.isPending} disabled={totalPending === 0}>
                Apply {totalPending} mapping(s)
              </Button>
              <Button variant="subtle" onClick={() => previewMutation.mutate()} loading={previewMutation.isPending}>
                Re-run preview
              </Button>
            </Group>
          </Stack>
        )}
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
  const sourceCategorySelectionAnchorRef = useRef<number | null>(null);
  const shiftKeyRef = useRef(false);
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
  const targetableCategories = categories.filter((c) => !c.sport_type);

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
                onClick={(e) => (shiftKeyRef.current = e.shiftKey)}
                onChange={() =>
                  toggleSelection(
                    c.id,
                    relevantCategories.map((rc) => rc.id),
                    shiftKeyRef.current,
                    setSelectedSourceCategories,
                    sourceCategorySelectionAnchorRef,
                  )
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
                data={targetableCategories.map((c) => ({ value: String(c.id), label: c.name }))}
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
  // Ground-truth substrings the admin can copy-paste straight out of the sample name, for a
  // naming convention the auto-detector can't figure out on its own (a written month name, or a
  // title sandwiched between unrelated noise on both sides). Optional - Suggest still works
  // without them, same as before, for names simple enough to auto-detect.
  const [titleHint, setTitleHint] = useState("");
  const [dateHint, setDateHint] = useState("");
  const [timeHint, setTimeHint] = useState("");
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
      setTitleHint("");
      setDateHint("");
      setTimeHint("");
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
        .post(`/api/playlists/${playlistId}/dummy-epg-rules/suggest`, {
          sample_name: name,
          title_hint: titleHint || undefined,
          date_hint: dateHint || undefined,
          time_hint: timeHint || undefined,
          timezone: newTimezone,
        })
        .then(
          (r) =>
            r.data as {
              suggested: boolean;
              pattern?: string;
              start?: string;
              title?: string;
              title_hint?: string | null;
              date_hint?: string | null;
              time_hint?: string | null;
            },
        ),
    onSuccess: (data) => {
      if (!data.suggested || !data.pattern) {
        setTestResult({
          matched: false,
          error:
            dateHint || timeHint
              ? "Couldn't find that title/date/time text in the sample name - check it's copied exactly."
              : "Couldn't find a date/time in that name to build a rule from.",
        });
        return;
      }
      setNewPattern(data.pattern);
      // Fill in exactly what the pattern was built from - either what auto-detection found, or
      // the hints just submitted, echoed straight back - so the admin can see and edit them
      // (e.g. correct a misdetected title span) and hit Suggest again, rather than typing hints
      // in blind from scratch.
      setTitleHint(data.title_hint ?? "");
      setDateHint(data.date_hint ?? "");
      setTimeHint(data.time_hint ?? "");
      setTestResult({ matched: true, error: null, start: data.start, title: data.title });
    },
  });

  // Opened from a channel's "Suggest Rule..." button: pre-fill the sample name and suggest
  // immediately, so the admin lands straight on a candidate pattern for that exact channel.
  useEffect(() => {
    if (opened && initialSampleName) {
      setSampleName(initialSampleName);
      setTitleHint("");
      setDateHint("");
      setTimeHint("");
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
    setTitleHint("");
    setDateHint("");
    setTimeHint("");
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

        <Text size="xs" c="dimmed" mt="xs">
          After hitting Suggest below, these fill in with exactly what the pattern was built
          from - edit any that are wrong (a written-out month like "Sep", or a title sitting
          between unrelated noise can trip up auto-detection) and hit Suggest again to rebuild
          the pattern from your correction instead of the original guess.
        </Text>
        <Group grow>
          <TextInput
            size="xs"
            label="Title"
            value={titleHint}
            onChange={(e) => setTitleHint(e.currentTarget.value)}
          />
          <TextInput
            size="xs"
            label="Date"
            value={dateHint}
            onChange={(e) => setDateHint(e.currentTarget.value)}
          />
          <TextInput
            size="xs"
            label="Time"
            value={timeHint}
            onChange={(e) => setTimeHint(e.currentTarget.value)}
          />
        </Group>

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

// Applying a Dummy EPG mode is currently one channel at a time in Channel Detail; this is the
// bulk equivalent, reusing the existing bulk-action endpoint. Once several channels are in
// "event" mode, the playlist's Dummy EPG Rules already apply to every one of them automatically
// (they're playlist-wide, not per-channel) - so this bulk mode-set is the missing piece for
// "apply a rule to multiple channels" rather than the rule itself needing any per-channel setup.
function BulkDummyEpgModal({
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
  const [mode, setMode] = useState<DummyEpgMode>("event");
  const [minutes, setMinutes] = useState<number | "">("");
  // "" = leave each channel's existing pinned rule as-is, "0" = explicitly clear it (try every
  // enabled rule), otherwise a specific rule id.
  const [ruleId, setRuleId] = useState("");

  const { data: dummyEpgRules } = useQuery<DummyEpgRule[]>({
    queryKey: ["dummy-epg-rules", playlistId],
    queryFn: () => api.get(`/api/playlists/${playlistId}/dummy-epg-rules`).then((r) => r.data),
    enabled: opened && mode === "event",
  });

  const mutation = useMutation({
    mutationFn: () =>
      api.post(`/api/playlists/${playlistId}/channels/bulk`, {
        channel_ids: channelIds,
        action: "set_dummy_epg_mode",
        dummy_epg_mode: mode,
        dummy_epg_program_minutes: minutes === "" ? null : minutes,
        ...(ruleId !== "" ? { dummy_epg_rule_id: ruleId === "0" ? null : Number(ruleId) } : {}),
      }),
    onSuccess: () => {
      onChanged();
      onClose();
      notifications.show({ message: `Set Dummy EPG mode for ${channelIds.length} channel(s)`, color: "green" });
    },
    onError: (err) =>
      notifications.show({
        message: (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail || "Request failed",
        color: "red",
      }),
  });

  return (
    <Modal opened={opened} onClose={onClose} title={`Set Dummy EPG Mode for ${channelIds.length} channel(s)`} size="sm">
      <Stack>
        <Select
          label="Mode"
          data={[
            { value: "inherit", label: "Inherit from category" },
            { value: "off", label: "Off" },
            { value: "name", label: "Channel name as program" },
            { value: "event", label: "Parse event date/time from name" },
          ]}
          value={mode}
          onChange={(v) => setMode((v as DummyEpgMode) ?? "event")}
        />
        <NumberInput
          label="Program length (minutes)"
          description="Leave blank to keep each channel's existing length"
          value={minutes}
          onChange={(v) => setMinutes(v === "" ? "" : Number(v))}
          min={5}
        />
        {mode === "event" && (
          <Select
            label="Rule"
            description="Leave unchanged to keep each channel's existing pinned rule"
            data={[
              { value: "", label: "Leave unchanged" },
              { value: "0", label: "Any enabled rule (default)" },
              ...(dummyEpgRules ?? []).map((r) => ({
                value: String(r.id),
                label: r.enabled ? r.name : `${r.name} (disabled)`,
              })),
            ]}
            value={ruleId}
            onChange={(v) => setRuleId(v ?? "")}
          />
        )}
        <Button onClick={() => mutation.mutate()} loading={mutation.isPending} disabled={channelIds.length === 0}>
          Apply to {channelIds.length} channel(s)
        </Button>
      </Stack>
    </Modal>
  );
}

interface EpgPreviewProgram {
  start: string;
  stop: string;
  title: string;
}

interface EpgPreviewChannel {
  channel_id: number;
  name: string;
  dummy_epg_mode: DummyEpgMode;
  programs: EpgPreviewProgram[];
}

interface EpgPreviewResult {
  hours: number;
  total_channels: number;
  truncated: boolean;
  channels: EpgPreviewChannel[];
}

// Shows exactly what XMLTV output would contain for this category right now - real programs
// where EPG-mapped, dummy-generated ones otherwise (including custom event rules and "Up Next"
// blocks) - computed server-side by the same code the real feed uses, so this can't drift from
// what a player actually pulls.
function EpgPreviewModal({
  opened,
  onClose,
  playlistId,
  categoryId,
  categoryName,
}: {
  opened: boolean;
  onClose: () => void;
  playlistId: string;
  categoryId: number;
  categoryName: string;
}) {
  const [hours, setHours] = useState(24);

  const { data, isLoading, isError } = useQuery<EpgPreviewResult>({
    queryKey: ["epg-preview", playlistId, categoryId, hours],
    queryFn: () =>
      api
        .get(`/api/playlists/${playlistId}/categories/${categoryId}/epg-preview`, { params: { hours } })
        .then((r) => r.data),
    enabled: opened,
  });

  return (
    <Modal opened={opened} onClose={onClose} title={`EPG Preview — ${categoryName}`} size="xl">
      <Stack>
        <Group justify="space-between" align="flex-end">
          <Select
            label="Window"
            data={[
              { value: "6", label: "Next 6 hours" },
              { value: "12", label: "Next 12 hours" },
              { value: "24", label: "Next 24 hours" },
              { value: "48", label: "Next 2 days" },
              { value: "72", label: "Next 3 days" },
            ]}
            value={String(hours)}
            onChange={(v) => setHours(Number(v) || 24)}
            w={200}
          />
          {data && (
            <Text size="xs" c="dimmed">
              {data.total_channels} channel(s){data.truncated ? ` — showing first ${data.channels.length}` : ""}
            </Text>
          )}
        </Group>

        {isLoading && (
          <Group justify="center" py="xl">
            <Loader size="sm" />
          </Group>
        )}
        {isError && (
          <Text size="sm" c="red">
            Failed to load preview.
          </Text>
        )}

        <ScrollArea h={520}>
          <Stack gap="sm">
            {data?.channels.map((ch) => (
              <Paper key={ch.channel_id} withBorder p="xs">
                <Group gap={6} mb={4}>
                  <Text size="sm" fw={600}>
                    {ch.name}
                  </Text>
                  <Badge size="xs" variant="outline">
                    {ch.dummy_epg_mode}
                  </Badge>
                </Group>
                {ch.programs.length === 0 ? (
                  <Text size="xs" c="dimmed">
                    No programs in this window (dummy EPG is off, or nothing mapped).
                  </Text>
                ) : (
                  <Table>
                    <Table.Tbody>
                      {ch.programs.map((p, i) => (
                        <Table.Tr key={i}>
                          <Table.Td w={260}>
                            <Text size="xs" c="dimmed">
                              {new Date(p.start).toLocaleString()} – {new Date(p.stop).toLocaleTimeString()}
                            </Text>
                          </Table.Td>
                          <Table.Td>
                            <Text size="xs">{p.title}</Text>
                          </Table.Td>
                        </Table.Tr>
                      ))}
                    </Table.Tbody>
                  </Table>
                )}
              </Paper>
            ))}
            {data && data.channels.length === 0 && (
              <Text size="sm" c="dimmed">
                No enabled channels in this category.
              </Text>
            )}
          </Stack>
        </ScrollArea>
      </Stack>
    </Modal>
  );
}

