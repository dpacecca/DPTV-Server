import { useState } from "react";
import { ActionIcon, Badge, Button, Group, Modal, Paper, Stack, Switch, Table, Text, TextInput, Title } from "@mantine/core";
import { TimeInput } from "@mantine/dates";
import { notifications } from "@mantine/notifications";
import { IconPlayerPlay, IconPlus, IconTrash } from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { SyncRun, SyncSchedule } from "../api/types";
import { EmptyState } from "../App";

export default function SchedulerPage() {
  const qc = useQueryClient();
  const [modalOpen, setModalOpen] = useState(false);
  const [time, setTime] = useState("06:00");
  const [label, setLabel] = useState("");

  const { data: schedules, isLoading } = useQuery<SyncSchedule[]>({
    queryKey: ["schedules"],
    queryFn: () => api.get("/api/schedules").then((r) => r.data),
  });

  const { data: runs } = useQuery<SyncRun[]>({
    queryKey: ["sync-runs"],
    queryFn: () => api.get("/api/sync-runs").then((r) => r.data),
    refetchInterval: 10000,
  });

  const createMutation = useMutation({
    mutationFn: () => api.post("/api/schedules", { label, time_of_day: `${time}:00` }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["schedules"] });
      setModalOpen(false);
      setLabel("");
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.delete(`/api/schedules/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["schedules"] }),
  });

  const runNowMutation = useMutation({
    mutationFn: () => api.post("/api/sync/run"),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["sync-runs"] });
      notifications.show({ message: "Sync completed", color: "green" });
    },
    onError: () => notifications.show({ message: "Sync failed", color: "red" }),
  });

  return (
    <Stack>
      <Group justify="space-between">
        <Title order={3}>Sync Scheduler</Title>
        <Group>
          <Button variant="light" leftSection={<IconPlayerPlay size={16} />} loading={runNowMutation.isPending} onClick={() => runNowMutation.mutate()}>
            Sync Now
          </Button>
          <Button leftSection={<IconPlus size={16} />} onClick={() => setModalOpen(true)}>
            Add Sync Time
          </Button>
        </Group>
      </Group>

      <Paper withBorder p="md">
        <Text fw={600} size="sm" mb="sm">
          Daily sync times
        </Text>
        {!isLoading && schedules?.length === 0 && <EmptyState text="No sync times scheduled. Add one or more times per day to keep channels and EPG fresh." />}
        {schedules && schedules.length > 0 && (
          <Table striped>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Time (UTC)</Table.Th>
                <Table.Th>Label</Table.Th>
                <Table.Th>Enabled</Table.Th>
                <Table.Th />
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {schedules.map((s) => (
                <Table.Tr key={s.id}>
                  <Table.Td>{s.time_of_day}</Table.Td>
                  <Table.Td>{s.label || "-"}</Table.Td>
                  <Table.Td>
                    <Switch checked={s.enabled} readOnly />
                  </Table.Td>
                  <Table.Td>
                    <ActionIcon variant="subtle" color="red" onClick={() => deleteMutation.mutate(s.id)}>
                      <IconTrash size={16} />
                    </ActionIcon>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        )}
      </Paper>

      <Paper withBorder p="md">
        <Text fw={600} size="sm" mb="sm">
          Recent sync runs
        </Text>
        {runs && runs.length > 0 ? (
          <Table striped>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Started</Table.Th>
                <Table.Th>Trigger</Table.Th>
                <Table.Th>Status</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {runs.map((r) => (
                <Table.Tr key={r.id}>
                  <Table.Td>{new Date(r.started_at).toLocaleString()}</Table.Td>
                  <Table.Td>{r.trigger}</Table.Td>
                  <Table.Td>
                    <Badge color={r.status === "success" ? "green" : r.status === "running" ? "blue" : "red"}>{r.status}</Badge>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        ) : (
          <EmptyState text="No sync runs yet." />
        )}
      </Paper>

      <Modal opened={modalOpen} onClose={() => setModalOpen(false)} title="Add Sync Time">
        <Stack>
          <TimeInput label="Time (UTC)" value={time} onChange={(e) => setTime(e.currentTarget.value)} />
          <TextInput label="Label (optional)" value={label} onChange={(e) => setLabel(e.currentTarget.value)} />
          <Button onClick={() => createMutation.mutate()}>Save</Button>
        </Stack>
      </Modal>
    </Stack>
  );
}
