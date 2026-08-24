import { useState } from "react";
import { ActionIcon, Badge, Button, Group, Modal, Paper, Stack, Switch, Table, TextInput, Title } from "@mantine/core";
import { IconPlus, IconTrash } from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { Playlist } from "../api/types";
import { EmptyState } from "../App";

export default function PlaylistsPage() {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [modalOpen, setModalOpen] = useState(false);
  const [name, setName] = useState("");

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
        <Button leftSection={<IconPlus size={16} />} onClick={() => setModalOpen(true)}>
          New Playlist
        </Button>
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
    </Stack>
  );
}
