import { useState } from "react";
import {
  ActionIcon,
  Badge,
  Button,
  Group,
  Modal,
  NumberInput,
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
import { IconPlus, IconRefresh, IconTrash } from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { Source, SourceType } from "../api/types";
import { EmptyState } from "../App";

const emptyForm = {
  name: "",
  type: "xtream" as SourceType,
  base_url: "",
  username: "",
  password: "",
  m3u_url: "",
  auto_clear_removed_days: null as number | null,
  auto_enable_new_groups: true,
  ignore_vod: false,
  ignore_series: false,
  provider_uses_tokens: false,
};

export default function SourcesPage() {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [modalOpen, setModalOpen] = useState(false);
  const [form, setForm] = useState(emptyForm);

  const { data: sources, isLoading } = useQuery<Source[]>({
    queryKey: ["sources"],
    queryFn: () => api.get("/api/sources").then((r) => r.data),
  });

  const createMutation = useMutation({
    mutationFn: () => api.post("/api/sources", form),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["sources"] });
      setModalOpen(false);
      setForm(emptyForm);
      notifications.show({ message: "Source added", color: "green" });
    },
    onError: () => notifications.show({ message: "Failed to add source", color: "red" }),
  });

  const syncMutation = useMutation({
    mutationFn: (id: number) => api.post(`/api/sources/${id}/sync`),
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: ["sources"] });
      const s = res.data;
      notifications.show({
        message: `Synced: +${s.channels_added} channels, ${s.channels_updated} updated, ${s.channels_removed} removed`,
        color: "green",
      });
    },
    onError: (err: unknown) => {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? "Sync failed";
      notifications.show({ message: msg, color: "red" });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.delete(`/api/sources/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["sources"] }),
  });

  return (
    <Stack>
      <Group justify="space-between">
        <Title order={3}>Sources</Title>
        <Button leftSection={<IconPlus size={16} />} onClick={() => setModalOpen(true)}>
          Add Source
        </Button>
      </Group>

      <Paper withBorder p="md">
        {!isLoading && sources?.length === 0 && <EmptyState text="No sources yet. Add an Xtream or M3U provider to get started." />}
        {sources && sources.length > 0 && (
          <Table striped highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Name</Table.Th>
                <Table.Th>Type</Table.Th>
                <Table.Th>Last Sync</Table.Th>
                <Table.Th>Status</Table.Th>
                <Table.Th />
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {sources.map((s) => (
                <Table.Tr key={s.id} style={{ cursor: "pointer" }}>
                  <Table.Td onClick={() => navigate(`/sources/${s.id}`)}>{s.name}</Table.Td>
                  <Table.Td onClick={() => navigate(`/sources/${s.id}`)}>
                    <Badge variant="light">{s.type}</Badge>
                  </Table.Td>
                  <Table.Td onClick={() => navigate(`/sources/${s.id}`)}>
                    {s.last_sync_at ? new Date(s.last_sync_at).toLocaleString() : "Never"}
                  </Table.Td>
                  <Table.Td onClick={() => navigate(`/sources/${s.id}`)}>
                    {s.last_sync_status && (
                      <Badge color={s.last_sync_status === "success" ? "green" : "red"}>{s.last_sync_status}</Badge>
                    )}
                  </Table.Td>
                  <Table.Td>
                    <Group gap="xs">
                      <ActionIcon
                        variant="subtle"
                        loading={syncMutation.isPending}
                        onClick={() => syncMutation.mutate(s.id)}
                      >
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

      <Modal opened={modalOpen} onClose={() => setModalOpen(false)} title="Add Source" size="lg">
        <Stack>
          <TextInput label="Name" value={form.name} onChange={(e) => setForm({ ...form, name: e.currentTarget.value })} required />
          <Select
            label="Type"
            data={[
              { value: "xtream", label: "Xtream Codes API (recommended)" },
              { value: "m3u", label: "M3U Playlist URL" },
            ]}
            value={form.type}
            onChange={(v) => setForm({ ...form, type: (v as SourceType) || "xtream" })}
          />
          {form.type === "xtream" ? (
            <>
              <TextInput
                label="Server URL"
                placeholder="http://provider.example.com:8080"
                value={form.base_url}
                onChange={(e) => setForm({ ...form, base_url: e.currentTarget.value })}
              />
              <Group grow>
                <TextInput label="Username" value={form.username} onChange={(e) => setForm({ ...form, username: e.currentTarget.value })} />
                <TextInput label="Password" value={form.password} onChange={(e) => setForm({ ...form, password: e.currentTarget.value })} />
              </Group>
            </>
          ) : (
            <TextInput label="M3U URL" value={form.m3u_url} onChange={(e) => setForm({ ...form, m3u_url: e.currentTarget.value })} />
          )}
          <Group grow>
            <Switch
              label="Ignore VOD"
              checked={form.ignore_vod}
              onChange={(e) => setForm({ ...form, ignore_vod: e.currentTarget.checked })}
            />
            <Switch
              label="Ignore Series"
              checked={form.ignore_series}
              onChange={(e) => setForm({ ...form, ignore_series: e.currentTarget.checked })}
            />
          </Group>
          <Switch
            label="Automatically enable new groups added by provider"
            checked={form.auto_enable_new_groups}
            onChange={(e) => setForm({ ...form, auto_enable_new_groups: e.currentTarget.checked })}
          />
          <Switch
            label="Provider uses tokens (match channels by name instead of stream id)"
            checked={form.provider_uses_tokens}
            onChange={(e) => setForm({ ...form, provider_uses_tokens: e.currentTarget.checked })}
          />
          <NumberInput
            label="Auto-clear channels removed by provider after (days)"
            placeholder="Leave blank to disable"
            value={form.auto_clear_removed_days ?? ""}
            onChange={(v) => setForm({ ...form, auto_clear_removed_days: v === "" ? null : Number(v) })}
            min={1}
          />
          <Text size="xs" c="dimmed">
            After adding, use the refresh icon on the sources list to load categories and channels.
          </Text>
          <Button onClick={() => createMutation.mutate()} loading={createMutation.isPending} disabled={!form.name}>
            Save
          </Button>
        </Stack>
      </Modal>
    </Stack>
  );
}
