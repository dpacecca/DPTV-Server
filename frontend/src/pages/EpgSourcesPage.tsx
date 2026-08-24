import { useState } from "react";
import { ActionIcon, Badge, Button, Group, Modal, NumberInput, Paper, Stack, Table, TextInput, Title } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconPlus, IconRefresh, IconTrash } from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { EpgSource } from "../api/types";
import { EmptyState } from "../App";

export default function EpgSourcesPage() {
  const qc = useQueryClient();
  const [modalOpen, setModalOpen] = useState(false);
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [refreshInterval, setRefreshInterval] = useState(720);

  const { data: sources, isLoading } = useQuery<EpgSource[]>({
    queryKey: ["epg-sources"],
    queryFn: () => api.get("/api/epg-sources").then((r) => r.data),
  });

  const createMutation = useMutation({
    mutationFn: () => api.post("/api/epg-sources", { name, url, refresh_interval_minutes: refreshInterval }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["epg-sources"] });
      setModalOpen(false);
      setName("");
      setUrl("");
    },
  });

  const refreshMutation = useMutation({
    mutationFn: (id: number) => api.post(`/api/epg-sources/${id}/refresh`),
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: ["epg-sources"] });
      notifications.show({ message: `Loaded ${res.data.channels} channels, ${res.data.programs} programs`, color: "green" });
    },
    onError: () => notifications.show({ message: "Refresh failed", color: "red" }),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.delete(`/api/epg-sources/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["epg-sources"] }),
  });

  return (
    <Stack>
      <Group justify="space-between">
        <Title order={3}>EPG Sources</Title>
        <Button leftSection={<IconPlus size={16} />} onClick={() => setModalOpen(true)}>
          Add EPG Source
        </Button>
      </Group>

      <Paper withBorder p="md">
        {!isLoading && sources?.length === 0 && <EmptyState text="No EPG sources yet. Add an XMLTV URL to enable guide data." />}
        {sources && sources.length > 0 && (
          <Table striped>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Name</Table.Th>
                <Table.Th>Channels</Table.Th>
                <Table.Th>Last Refreshed</Table.Th>
                <Table.Th>Status</Table.Th>
                <Table.Th />
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {sources.map((s) => (
                <Table.Tr key={s.id}>
                  <Table.Td>{s.name}</Table.Td>
                  <Table.Td>{s.channel_count}</Table.Td>
                  <Table.Td>{s.last_refreshed_at ? new Date(s.last_refreshed_at).toLocaleString() : "Never"}</Table.Td>
                  <Table.Td>
                    {s.last_refresh_status && (
                      <Badge color={s.last_refresh_status === "success" ? "green" : "red"}>{s.last_refresh_status}</Badge>
                    )}
                  </Table.Td>
                  <Table.Td>
                    <Group gap="xs">
                      <ActionIcon variant="subtle" loading={refreshMutation.isPending} onClick={() => refreshMutation.mutate(s.id)}>
                        <IconRefresh size={16} />
                      </ActionIcon>
                      <ActionIcon variant="subtle" color="red" onClick={() => deleteMutation.mutate(s.id)}>
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

      <Modal opened={modalOpen} onClose={() => setModalOpen(false)} title="Add EPG Source">
        <Stack>
          <TextInput label="Name" value={name} onChange={(e) => setName(e.currentTarget.value)} required />
          <TextInput label="XMLTV URL" value={url} onChange={(e) => setUrl(e.currentTarget.value)} required />
          <NumberInput
            label="Refresh interval (minutes)"
            value={refreshInterval}
            onChange={(v) => setRefreshInterval(Number(v) || 720)}
            min={15}
          />
          <Button onClick={() => createMutation.mutate()} loading={createMutation.isPending} disabled={!name || !url}>
            Save
          </Button>
        </Stack>
      </Modal>
    </Stack>
  );
}
