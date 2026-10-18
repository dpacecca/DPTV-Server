import { useState } from "react";
import {
  ActionIcon,
  Badge,
  Button,
  FileInput,
  Group,
  Loader,
  Modal,
  Paper,
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
import { IconCopy, IconDownload, IconPlus, IconTrash, IconUpload } from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { Playlist, Source, XcUser } from "../api/types";
import { EmptyState } from "../App";

export default function PlaylistsPage() {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [modalOpen, setModalOpen] = useState(false);
  const [name, setName] = useState("");
  const [importM3uOpen, setImportM3uOpen] = useState(false);
  const [exportTarget, setExportTarget] = useState<Playlist | null>(null);

  const { data: playlists, isLoading } = useQuery<Playlist[]>({
    queryKey: ["playlists"],
    queryFn: () => api.get("/api/playlists").then((r) => r.data),
  });

  const createMutation = useMutation({
    mutationFn: () => api.post("/api/playlists", { name }),
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: ["playlists"] });
      setModalOpen(false);
      setName("");
      navigate(`/playlists/${res.data.id}`);
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.delete(`/api/playlists/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["playlists"] }),
  });

  const duplicateMutation = useMutation({
    mutationFn: (id: number) => api.post<Playlist>(`/api/playlists/${id}/duplicate`).then((r) => r.data),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ["playlists"] });
      notifications.show({ message: `Duplicated as "${data.name}"`, color: "green" });
    },
    onError: () => notifications.show({ message: "Duplicate failed", color: "red" }),
  });

  return (
    <Stack>
      <Group justify="space-between">
        <Title order={3}>Playlists</Title>
        <Group gap="xs">
          <Button variant="light" leftSection={<IconUpload size={16} />} onClick={() => setImportM3uOpen(true)}>
            Import M3U...
          </Button>
          <Button leftSection={<IconPlus size={16} />} onClick={() => setModalOpen(true)}>
            New Playlist
          </Button>
        </Group>
      </Group>

      <Paper withBorder p="md">
        {!isLoading && playlists?.length === 0 && (
          <EmptyState text="No playlists yet. Create one, then import channels from your sources." />
        )}
        {playlists && playlists.length > 0 && (
          <Table striped highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Name</Table.Th>
                <Table.Th>Categories</Table.Th>
                <Table.Th>Channels</Table.Th>
                <Table.Th>Enabled</Table.Th>
                <Table.Th>XC Server</Table.Th>
                <Table.Th />
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {playlists.map((p) => (
                <Table.Tr key={p.id} style={{ cursor: "pointer" }}>
                  <Table.Td onClick={() => navigate(`/playlists/${p.id}`)}>{p.name}</Table.Td>
                  <Table.Td onClick={() => navigate(`/playlists/${p.id}`)}>{p.category_count}</Table.Td>
                  <Table.Td onClick={() => navigate(`/playlists/${p.id}`)}>{p.channel_count}</Table.Td>
                  <Table.Td onClick={() => navigate(`/playlists/${p.id}`)}>
                    <Badge color={p.enabled ? "green" : "gray"}>{p.enabled ? "Enabled" : "Disabled"}</Badge>
                  </Table.Td>
                  <Table.Td onClick={() => navigate(`/playlists/${p.id}`)}>
                    <Badge color={p.xc_enabled ? "indigo" : "gray"} variant="light">
                      {p.xc_enabled ? "On" : "Off"}
                    </Badge>
                  </Table.Td>
                  <Table.Td>
                    <Group gap={4} wrap="nowrap">
                      <Tooltip label="Duplicate">
                        <ActionIcon variant="subtle" loading={duplicateMutation.isPending} onClick={() => duplicateMutation.mutate(p.id)}>
                          <IconCopy size={16} />
                        </ActionIcon>
                      </Tooltip>
                      <Tooltip label="Export M3U">
                        <ActionIcon variant="subtle" onClick={() => setExportTarget(p)}>
                          <IconDownload size={16} />
                        </ActionIcon>
                      </Tooltip>
                      <Tooltip label="Delete">
                        <ActionIcon variant="subtle" color="red" onClick={() => deleteMutation.mutate(p.id)}>
                          <IconTrash size={16} />
                        </ActionIcon>
                      </Tooltip>
                    </Group>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        )}
      </Paper>

      <Modal opened={modalOpen} onClose={() => setModalOpen(false)} title="New Playlist">
        <Stack>
          <TextInput label="Name" value={name} onChange={(e) => setName(e.currentTarget.value)} required />
          <Switch label="XC server enabled" defaultChecked disabled />
          <Button onClick={() => createMutation.mutate()} loading={createMutation.isPending} disabled={!name}>
            Create
          </Button>
        </Stack>
      </Modal>

      <ImportM3uModal opened={importM3uOpen} onClose={() => setImportM3uOpen(false)} />
      <ExportM3uModal playlist={exportTarget} onClose={() => setExportTarget(null)} />
    </Stack>
  );
}

// Triggers a browser download of the playlist's generated M3U, using one XC user's credentials
// to build the playable stream URLs (the output endpoint requires one - see output_m3u). Skips
// straight to the download with no picker when there's only one XC user to choose from.
function ExportM3uModal({ playlist, onClose }: { playlist: Playlist | null; onClose: () => void }) {
  const [xcUserId, setXcUserId] = useState<string | null>(null);
  const [downloading, setDownloading] = useState(false);

  const { data: xcUsers, isLoading } = useQuery<XcUser[]>({
    queryKey: ["xc-users"],
    queryFn: () => api.get("/api/xc-users").then((r) => r.data),
    enabled: playlist !== null,
  });

  const singleUser = xcUsers?.length === 1 ? xcUsers[0] : null;

  async function download(userId: number) {
    if (!playlist) return;
    setDownloading(true);
    try {
      const res = await api.get(`/api/playlists/${playlist.id}/output/m3u`, {
        params: { xc_user_id: userId },
        responseType: "blob",
      });
      const url = URL.createObjectURL(res.data as Blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = playlist.m3u_filename || `${playlist.name}.m3u`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      handleClose();
    } catch {
      notifications.show({ message: "Export failed", color: "red" });
    } finally {
      setDownloading(false);
    }
  }

  function handleClose() {
    setXcUserId(null);
    onClose();
  }

  return (
    <Modal opened={playlist !== null} onClose={handleClose} title={`Export M3U — ${playlist?.name ?? ""}`}>
      <Stack>
        {isLoading && (
          <Group justify="center" py="md">
            <Loader size="sm" />
          </Group>
        )}
        {!isLoading && xcUsers?.length === 0 && (
          <Text size="sm" c="dimmed">
            No XC users exist yet - create one on the XC Users page first. Every exported stream URL
            is generated with one XC user's credentials baked in.
          </Text>
        )}
        {!isLoading && singleUser && (
          <>
            <Text size="sm" c="dimmed">
              Stream URLs in the file will use XC user "{singleUser.username}".
            </Text>
            <Button leftSection={<IconDownload size={16} />} loading={downloading} onClick={() => download(singleUser.id)}>
              Download
            </Button>
          </>
        )}
        {!isLoading && xcUsers && xcUsers.length > 1 && (
          <>
            <Select
              label="Build stream URLs using XC user"
              placeholder="Choose an XC user"
              data={xcUsers.map((u) => ({ value: String(u.id), label: u.username }))}
              value={xcUserId}
              onChange={setXcUserId}
              required
            />
            <Button
              leftSection={<IconDownload size={16} />}
              loading={downloading}
              disabled={!xcUserId}
              onClick={() => xcUserId && download(Number(xcUserId))}
            >
              Download
            </Button>
          </>
        )}
      </Stack>
    </Modal>
  );
}

interface ImportM3uResult {
  playlist_id: number;
  categories: number;
  channels: number;
  matched: number;
  unmatched: number;
  unmatched_names: string[];
}

// Brings in a playlist authored elsewhere (e.g. exported from IPTVBoss) as a new playlist here,
// matching each channel back to a source already synced in DPTV-Server by its stream URL - so
// the result keeps working through that source (re-syncs, survives credential rotation) instead
// of being frozen to whatever URLs happened to be in the file.
function ImportM3uModal({ opened, onClose }: { opened: boolean; onClose: () => void }) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [file, setFile] = useState<File | null>(null);
  const [playlistName, setPlaylistName] = useState("");
  const [sourceId, setSourceId] = useState<string | null>(null);
  const [result, setResult] = useState<ImportM3uResult | null>(null);

  const { data: sources } = useQuery<Source[]>({
    queryKey: ["sources"],
    queryFn: () => api.get("/api/sources").then((r) => r.data),
    enabled: opened,
  });

  const importMutation = useMutation({
    mutationFn: () => {
      const form = new FormData();
      form.append("file", file as File);
      form.append("source_id", sourceId as string);
      form.append("playlist_name", playlistName);
      return api.post<ImportM3uResult>("/api/playlists/import-m3u", form).then((r) => r.data);
    },
    onSuccess: (data) => {
      setResult(data);
      qc.invalidateQueries({ queryKey: ["playlists"] });
    },
    onError: (err) => {
      const detail = (err as { response?: { data?: { detail?: string }; status?: number } })?.response;
      const message =
        detail?.status === 413
          ? "That file is too large for the server to accept right now (ask whoever runs this server to raise nginx's client_max_body_size)."
          : detail?.data?.detail || "Import failed - check the file is a valid M3U and try again.";
      notifications.show({ message, color: "red", autoClose: 8000 });
    },
  });

  function handleClose() {
    const playlistId = result?.playlist_id;
    setFile(null);
    setPlaylistName("");
    setSourceId(null);
    setResult(null);
    onClose();
    if (playlistId) navigate(`/playlists/${playlistId}`);
  }

  return (
    <Modal opened={opened} onClose={handleClose} title="Import M3U Playlist" size="md">
      <Stack>
        <Text size="sm" c="dimmed">
          Import an M3U file exported from another tool (e.g. IPTVBoss) as a new playlist,
          matching each channel back to a source you've already synced here by its stream URL -
          so it keeps working through that source instead of the URLs baked into the file.
          Channels that can't be matched still import fine, just using their own URL directly.
        </Text>

        <FileInput
          label="M3U file"
          placeholder="Choose file..."
          accept=".m3u,.m3u8,text/plain"
          value={file}
          onChange={setFile}
          disabled={!!result}
          required
        />
        <TextInput
          label="Playlist name"
          value={playlistName}
          onChange={(e) => setPlaylistName(e.currentTarget.value)}
          disabled={!!result}
          required
        />
        <Select
          label="Match against source"
          placeholder="Choose a source"
          data={(sources ?? []).map((s) => ({ value: String(s.id), label: s.name }))}
          value={sourceId}
          onChange={setSourceId}
          disabled={!!result}
          required
        />

        {result && (
          <Stack gap={4}>
            <Text size="sm" c="green">
              Imported {result.channels} channel(s) into {result.categories} categor{result.categories === 1 ? "y" : "ies"}
              {" — "}matched {result.matched}, {result.unmatched} unmatched.
            </Text>
            {result.unmatched_names.length > 0 && (
              <Text size="xs" c="dimmed">
                Unmatched: {result.unmatched_names.join(", ")}
                {result.unmatched > result.unmatched_names.length ? ", ..." : ""}
              </Text>
            )}
          </Stack>
        )}

        {!result ? (
          <Button
            onClick={() => importMutation.mutate()}
            loading={importMutation.isPending}
            disabled={!file || !playlistName || !sourceId}
          >
            Import
          </Button>
        ) : (
          <Button onClick={handleClose}>Open Playlist</Button>
        )}
      </Stack>
    </Modal>
  );
}
