import { useState } from "react";
import {
  Alert,
  Badge,
  Button,
  Group,
  NumberInput,
  Paper,
  PasswordInput,
  Select,
  Stack,
  Text,
  TextInput,
  Title,
  Tooltip,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconAlertTriangle, IconInfoCircle } from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";

interface SettingField {
  key: string;
  group: string;
  type: "string" | "int" | "float" | "secret" | "timezone";
  label: string;
  description: string;
  value: string | number | boolean | null;
  default: string | number | null;
  env_override: boolean;
}

interface ExcludedField {
  key: string;
  reason: string;
}

interface SettingsResponse {
  fields: SettingField[];
  excluded: ExcludedField[];
}

// Order groups appear in - anything not listed here (shouldn't happen) falls in after.
const GROUP_ORDER = ["Server", "Security", "Live Sport", "Duplicate Scanning", "Logging"];

export default function SettingsPage() {
  const qc = useQueryClient();
  // Pending edits, keyed by field key - only what the admin has actually touched this visit, so
  // Save sends exactly (and only) what changed rather than re-sending every field's current value.
  const [pending, setPending] = useState<Record<string, string | number>>({});

  const { data, isLoading } = useQuery<SettingsResponse>({
    queryKey: ["settings"],
    queryFn: () => api.get("/api/settings").then((r) => r.data),
  });

  const { data: timezones } = useQuery<string[]>({
    queryKey: ["timezones"],
    queryFn: () => api.get("/api/settings/timezones").then((r) => r.data),
    staleTime: Infinity,
  });

  const saveMutation = useMutation({
    mutationFn: () => api.patch("/api/settings", pending),
    onSuccess: () => {
      notifications.show({ message: "Settings saved", color: "green" });
      setPending({});
      qc.invalidateQueries({ queryKey: ["settings"] });
    },
    onError: (err) => {
      const message =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail || "Save failed";
      notifications.show({ message, color: "red" });
    },
  });

  if (isLoading || !data) {
    return <Text c="dimmed">Loading settings...</Text>;
  }

  const groups = new Map<string, SettingField[]>();
  for (const field of data.fields) {
    if (!groups.has(field.group)) groups.set(field.group, []);
    groups.get(field.group)!.push(field);
  }
  const orderedGroups = [...GROUP_ORDER, ...[...groups.keys()].filter((g) => !GROUP_ORDER.includes(g))].filter((g) =>
    groups.has(g),
  );

  const hasChanges = Object.keys(pending).length > 0;

  function setField(key: string, value: string | number) {
    setPending((prev) => ({ ...prev, [key]: value }));
  }

  return (
    <Stack>
      <Group justify="space-between">
        <Title order={3}>Settings</Title>
        <Button onClick={() => saveMutation.mutate()} loading={saveMutation.isPending} disabled={!hasChanges}>
          Save Changes
        </Button>
      </Group>
      <Text size="xs" c="dimmed">
        These write directly to the server's .env file and take effect immediately, without a
        restart - editing this here is the same as editing that file by hand.
      </Text>

      {orderedGroups.map((group) => (
        <Paper key={group} withBorder p="md">
          <Title order={5} mb="sm">
            {group}
          </Title>
          <Stack gap="sm">
            {groups.get(group)!.map((field) => (
              <SettingFieldInput
                key={field.key}
                field={field}
                pendingValue={pending[field.key]}
                timezones={timezones}
                onChange={(v) => setField(field.key, v)}
              />
            ))}
          </Stack>
        </Paper>
      ))}

      {data.excluded.length > 0 && (
        <Paper withBorder p="md">
          <Title order={5} mb="sm">
            Not editable here
          </Title>
          <Stack gap="xs">
            {data.excluded.map((f) => (
              <Alert key={f.key} icon={<IconInfoCircle size={14} />} color="gray" variant="light" py={6}>
                <Text size="xs" fw={600}>
                  {f.key}
                </Text>
                <Text size="xs" c="dimmed">
                  {f.reason}
                </Text>
              </Alert>
            ))}
          </Stack>
        </Paper>
      )}
    </Stack>
  );
}

function SettingFieldInput({
  field,
  pendingValue,
  timezones,
  onChange,
}: {
  field: SettingField;
  pendingValue: string | number | undefined;
  timezones: string[] | undefined;
  onChange: (v: string | number) => void;
}) {
  const currentValue = pendingValue ?? (field.type === "secret" ? "" : (field.value as string | number));
  const isDirty = pendingValue !== undefined;

  const labelRow = (
    <Group gap={6}>
      <Text size="sm" fw={600}>
        {field.label}
      </Text>
      {field.env_override && (
        <Tooltip label="A real environment variable is already setting this - changes here won't take effect until that variable is removed from wherever this server actually gets its environment (systemd unit, Docker compose, etc).">
          <Badge size="xs" color="orange" variant="light" leftSection={<IconAlertTriangle size={10} />}>
            env override active
          </Badge>
        </Tooltip>
      )}
      {isDirty && (
        <Badge size="xs" color="indigo" variant="light">
          unsaved
        </Badge>
      )}
    </Group>
  );

  if (field.type === "secret") {
    return (
      <Stack gap={2}>
        {labelRow}
        <PasswordInput
          size="xs"
          placeholder={field.value ? "Already set - leave blank to keep it" : "Not set"}
          value={(currentValue as string) ?? ""}
          onChange={(e) => onChange(e.currentTarget.value)}
        />
        <Text size="xs" c="dimmed">
          {field.description}
        </Text>
      </Stack>
    );
  }

  if (field.type === "timezone") {
    return (
      <Stack gap={2}>
        {labelRow}
        <Select
          size="xs"
          searchable
          data={timezones ?? []}
          value={(currentValue as string) ?? "UTC"}
          onChange={(v) => onChange(v ?? "UTC")}
        />
        <Text size="xs" c="dimmed">
          {field.description}
        </Text>
      </Stack>
    );
  }

  if (field.type === "int" || field.type === "float") {
    return (
      <Stack gap={2}>
        {labelRow}
        <NumberInput
          size="xs"
          value={currentValue as number}
          onChange={(v) => onChange(typeof v === "number" ? v : (field.default as number))}
          decimalScale={field.type === "float" ? 2 : 0}
          step={field.type === "float" ? 0.5 : 1}
        />
        <Text size="xs" c="dimmed">
          {field.description}
        </Text>
      </Stack>
    );
  }

  return (
    <Stack gap={2}>
      {labelRow}
      <TextInput size="xs" value={(currentValue as string) ?? ""} onChange={(e) => onChange(e.currentTarget.value)} />
      <Text size="xs" c="dimmed">
        {field.description}
      </Text>
    </Stack>
  );
}
