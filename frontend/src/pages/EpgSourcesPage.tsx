import { useState } from "react";
import {
  ActionIcon,
  Badge,
  Button,
  Group,
  Modal,
  NumberInput,
  Paper,
  Stack,
  Table,
  TextInput,
  Title,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconEdit, IconPlus, IconRefresh, IconTrash } from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, refreshAllEpgSources, refreshEpgSource } from "../api/client";
import type { EpgSource } from "../api/types";
import { EmptyState } from "../App";

export default function EpgSourcesPage() {
  const qc = useQueryClient();
  const [modalOpen, setModalOpen] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [refreshInterval, setRefreshInterval] = useState(720);

  const { data: sources, isLoading } = useQuery<EpgSource[]>({
    queryKey: ["epg-sources"],
    queryFn: () => api.get("/api/epg-sources").then((r) => r.data),
  });

  const resetForm = () => {
    setEditingId(null);
    setName("");
    setUrl("");
    setRefreshInterval(720);
  };

  const openAddModal = () => {
    resetForm();
    setModalOpen(true);
  };

  const openEditModal = (s: EpgSource) => {
    setEditingId(s.id);
    setName(s.name);
    setUrl(s.url);
    setRefreshInterval(s.refresh_interval_minutes);
    setModalOpen(true);
  };

  const saveMutation = useMutation({
    mutationFn: () => {
      const payload = { name, url, refresh_interval_minutes: refreshInterval };
      return editingId ? api.patch(`/api/epg-sources/${editingId}`, payload) : api.post("/api/epg-sources", payload);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["epg-sources"] });
      setModalOpen(false);
      resetForm();
    },
    onError: () =>
      notifications.show({ message: editingId ? "Failed to update EPG source" : "Failed to create EPG source", color: "red" }),
  });

  const refreshMutation = useMutation({
    mutationFn: (id: number) => refreshEpgSource(id),
    onSuccess: (result) => {
      qc.invalidateQueries({ queryKey: ["epg-sources"] });
      notifications.show({ message: `Loaded ${result.channels} channels, ${result.programs} programs`, color: "green" });
    },
    onError: (err: any) =>
      notifications.show({ message: err?.response?.data?.detail || err?.message || "Refresh failed", color: "red" }),
  });

  const refreshAllMutation = useMutation({
    mutationFn: () => refreshAllEpgSources(),
    onSuccess: ({ epg_sources: refreshed, errors }) => {
      qc.invalidateQueries({ queryKey: ["epg-sources"] });
      const count = Object.keys(refreshed).length;
      notifications.show({
        message: errors.length
          ? `Refreshed ${count} EPG source(s), ${errors.length} failed: ${errors.join("; ")}`
          : `Refreshed ${count} EPG source(s)`,
        color: errors.length ? "yellow" : "green",
      });
    },
    onError: (err: any) =>
      notifications.show({ message: err?.response?.data?.detail || err?.message || "Refresh all failed", color: "red" }),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.delete(`/api/epg-sources/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["epg-sources"] }),
  });

  const canSave = !!name && !!url;

  return (
    <Stack>
      <Group justify="space-between">
        <Title order={3}>EPG Sources</Title>
        <Group gap="xs">
          <Button
            variant="default"
            leftSection={<IconRefresh size={16} />}
            loading={refreshAllMutation.isPending}
            disabled={!sources || sources.length === 0}
            onClick={() => refreshAllMutation.mutate()}
          >
            Update All
          </Button>
          <Button leftSection={<IconPlus size={16} />} onClick={openAddModal}>
            Add EPG Source
          </Button>
        </Group>
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
                      <ActionIcon
                        variant="subtle"
                        loading={refreshMutation.isPending && refreshMutation.variables === s.id}
                        onClick={() => refreshMutation.mutate(s.id)}
                      >
                        <IconRefresh size={16} />
                      </ActionIcon>
                      <ActionIcon variant="subtle" onClick={() => openEditModal(s)}>
                        <IconEdit size={16} />
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

      <Modal
        opened={modalOpen}
        onClose={() => {
          setModalOpen(false);
          resetForm();
        }}
        title={editingId ? "Edit EPG Source" : "Add EPG Source"}
        size="lg"
      >
        <Stack>
          <TextInput label="Name" value={name} onChange={(e) => setName(e.currentTarget.value)} required />

          <TextInput
            label="XMLTV URL"
            description="Plain .xml or gzip-compressed .xml.gz both work"
            value={url}
            onChange={(e) => setUrl(e.currentTarget.value)}
            required
          />

          <NumberInput
            label="Refresh interval (minutes)"
            value={refreshInterval}
            onChange={(v) => setRefreshInterval(Number(v) || 720)}
            min={15}
          />

          <Button onClick={() => saveMutation.mutate()} loading={saveMutation.isPending} disabled={!canSave}>
            Save
          </Button>
        </Stack>
      </Modal>
    </Stack>
  );
}
