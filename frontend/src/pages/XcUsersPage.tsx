import { useState } from "react";
import {
  ActionIcon,
  Badge,
  Button,
  CopyButton,
  Group,
  Modal,
  Paper,
  Stack,
  Switch,
  Table,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import { IconCopy, IconLink, IconPlus, IconTrash } from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { API_BASE, api } from "../api/client";
import type { Playlist, XcUser } from "../api/types";

// API_BASE is empty in normal deployments (the UI and the XC API share an origin, whether
// that's the vite dev proxy or the production nginx container), so window.location.origin is
// the real, pasteable host:port for these links. Only an explicit VITE_API_BASE_URL overrides it.
const LINKS_BASE = API_BASE || window.location.origin;
import { EmptyState } from "../App";

export default function XcUsersPage() {
  const qc = useQueryClient();
  const [modalOpen, setModalOpen] = useState(false);
  const [linksUser, setLinksUser] = useState<XcUser | null>(null);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");

  const { data: users, isLoading } = useQuery<XcUser[]>({
    queryKey: ["xc-users"],
    queryFn: () => api.get("/api/xc-users").then((r) => r.data),
  });

  const { data: playlists } = useQuery<Playlist[]>({
    queryKey: ["playlists"],
    queryFn: () => api.get("/api/playlists").then((r) => r.data),
  });

  const createMutation = useMutation({
    mutationFn: () => api.post("/api/xc-users", { username, password }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["xc-users"] });
      setModalOpen(false);
      setUsername("");
      setPassword("");
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.delete(`/api/xc-users/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["xc-users"] }),
  });

  const toggleEnabledMutation = useMutation({
    mutationFn: (user: XcUser) => api.put(`/api/xc-users/${user.id}`, { ...user, enabled: !user.enabled }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["xc-users"] }),
  });

  const linkPlaylistMutation = useMutation({
    mutationFn: ({ userId, playlistId, enabled }: { userId: number; playlistId: number; enabled: boolean }) =>
      api.post(`/api/xc-users/${userId}/playlists`, { playlist_id: playlistId, enabled }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["xc-users"] }),
  });

  return (
    <Stack>
      <Group justify="space-between">
        <Title order={3}>XC Users</Title>
        <Button leftSection={<IconPlus size={16} />} onClick={() => setModalOpen(true)}>
          Add User
        </Button>
      </Group>

      <Paper withBorder p="md">
        {!isLoading && users?.length === 0 && (
          <EmptyState text="No XC users yet. Add one, then give its credentials to your IPTV player app." />
        )}
        {users && users.length > 0 && (
          <Table striped>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Username</Table.Th>
                <Table.Th>Password</Table.Th>
                <Table.Th>Playlists</Table.Th>
                <Table.Th>Enabled</Table.Th>
                <Table.Th />
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {users.map((u) => (
                <Table.Tr key={u.id}>
                  <Table.Td>{u.username}</Table.Td>
                  <Table.Td>{u.password}</Table.Td>
                  <Table.Td>
                    <Group gap={4}>
                      {(playlists ?? []).map((p) => {
                        const link = u.playlists.find((l) => l.playlist_id === p.id);
                        const enabled = !!link?.enabled;
                        return (
                          <Badge
                            key={p.id}
                            variant={enabled ? "filled" : "outline"}
                            color={enabled ? "indigo" : "gray"}
                            style={{ cursor: "pointer" }}
                            onClick={() => linkPlaylistMutation.mutate({ userId: u.id, playlistId: p.id, enabled: !enabled })}
                          >
                            {p.name}
                          </Badge>
                        );
                      })}
                    </Group>
                  </Table.Td>
                  <Table.Td>
                    <Switch checked={u.enabled} onChange={() => toggleEnabledMutation.mutate(u)} />
                  </Table.Td>
                  <Table.Td>
                    <Group gap="xs">
                      <ActionIcon variant="subtle" onClick={() => setLinksUser(u)}>
                        <IconLink size={16} />
                      </ActionIcon>
                      <ActionIcon variant="subtle" color="red" onClick={() => deleteMutation.mutate(u.id)}>
                        <IconTrash size={16} />
                      </ActionIcon>
                    </Group>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        )}
      </Paper>

      <Modal opened={modalOpen} onClose={() => setModalOpen(false)} title="Add XC User">
        <Stack>
          <TextInput label="Username" value={username} onChange={(e) => setUsername(e.currentTarget.value)} required />
          <TextInput label="Password" value={password} onChange={(e) => setPassword(e.currentTarget.value)} required />
          <Button onClick={() => createMutation.mutate()} disabled={!username || !password}>
            Create
          </Button>
        </Stack>
      </Modal>

      <Modal opened={!!linksUser} onClose={() => setLinksUser(null)} title="Connection Links" size="lg">
        {linksUser && (
          <Stack>
            <LinkRow label="Xtream / player_api URL" value={`${LINKS_BASE}/player_api.php?username=${linksUser.username}&password=${linksUser.password}`} />
            <LinkRow label="M3U Playlist" value={`${LINKS_BASE}/get.php?username=${linksUser.username}&password=${linksUser.password}&type=m3u_plus&output=ts`} />
            <LinkRow label="XMLTV EPG" value={`${LINKS_BASE}/xmltv.php?username=${linksUser.username}&password=${linksUser.password}`} />
            <Text size="xs" c="dimmed">
              Use the Xtream URL, username, and password directly in players like TiviMate, IPTV Smarters, etc.
            </Text>
          </Stack>
        )}
      </Modal>
    </Stack>
  );
}

function LinkRow({ label, value }: { label: string; value: string }) {
  return (
    <Stack gap={4}>
      <Text size="xs" fw={600} c="dimmed">
        {label}
      </Text>
      <Group gap="xs">
        <TextInput value={value} readOnly style={{ flex: 1 }} />
        <CopyButton value={value}>
          {({ copied, copy }) => (
            <ActionIcon variant="light" color={copied ? "green" : "indigo"} onClick={copy}>
              <IconCopy size={16} />
            </ActionIcon>
          )}
        </CopyButton>
      </Group>
    </Stack>
  );
}
