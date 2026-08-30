import { useState } from "react";
import {
  ActionIcon,
  Badge,
  Button,
  FileInput,
  Group,
  Modal,
  Paper,
  Select,
  Stack,
  Switch,
  Table,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconPlus, IconTrash, IconUpload } from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { Playlist, Source } from "../api/types";
import { EmptyState } from "../App";

export default function PlaylistsPage() {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [modalOpen, setModalOpen] = useState(false);
  const [name, setName] = useState("");
  const [importM3uOpen, setImportM3uOpen] = useState(false);

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
                    <ActionIcon variant="subtle" color="red" onClick={() => deleteMutation.mutate(p.id)}>
                      <IconTrash size={16} />
                    </ActionIcon>
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
    </Stack>
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
