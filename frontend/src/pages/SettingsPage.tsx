import { useState } from "react";
import {
  ActionIcon,
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
import { IconAlertTriangle, IconArrowDown, IconArrowUp, IconInfoCircle, IconPlus, IconX } from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";

interface SettingField {
  key: string;
  group: string;
  type: "string" | "int" | "float" | "secret" | "epg_layout";
  label: string;
  description: string;
  value: string | number | boolean | null;
  default: string | number | null;
  env_override: boolean;
}

// EPG_LAYOUT_TOKENS in backend/app/services/epg_writer.py - keep in sync.
const EPG_LAYOUT_TOKEN_LABELS: Record<string, string> = {
  title: "Title",
  subtitle_sports: "Subtitle (sports)",
  subtitle_nonsports: "Subtitle (non-sports)",
  description: "Description",
  episode_number: "Episode number",
  movie_year: "Year",
  episode_or_year: "Year (Movies) / Episode (TV Shows)",
  new_tag: "“NEW” tag",
  next_line: "— line break —",
};
const EPG_LAYOUT_TOKENS = Object.keys(EPG_LAYOUT_TOKEN_LABELS);

interface ExcludedField {
  key: string;
  reason: string;
}

interface SettingsResponse {
  fields: SettingField[];
  excluded: ExcludedField[];
}

// Order groups appear in - anything not listed here (shouldn't happen) falls in after.
const GROUP_ORDER = ["Server", "Security", "Duplicate Scanning", "Logging", "Integrations"];

export default function SettingsPage() {
  const qc = useQueryClient();
  // Pending edits, keyed by field key - only what the admin has actually touched this visit, so
  // Save sends exactly (and only) what changed rather than re-sending every field's current value.
  const [pending, setPending] = useState<Record<string, string | number>>({});

  const { data, isLoading } = useQuery<SettingsResponse>({
    queryKey: ["settings"],
    queryFn: () => api.get("/api/settings").then((r) => r.data),
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
    if (field.type === "epg_layout") continue; // rendered by EpgLayoutSection below instead
    if (!groups.has(field.group)) groups.set(field.group, []);
    groups.get(field.group)!.push(field);
  }
  const titleLayoutField = data.fields.find((f) => f.key === "epg_title_layout");
  const descriptionLayoutField = data.fields.find((f) => f.key === "epg_description_layout");
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
                onChange={(v) => setField(field.key, v)}
              />
            ))}
          </Stack>
        </Paper>
      ))}

      {titleLayoutField && descriptionLayoutField && (
        <Paper withBorder p="md">
          <Title order={5} mb={4}>
            EPG Guide
          </Title>
          <Text size="xs" c="dimmed" mb="sm">
            Controls how a real (non-dummy) EPG program's title and description are assembled for
            XMLTV output, the XC API, and the admin UI's own EPG preview - mirroring IPTVBoss's
            own EPG layout. A field with nothing to show for a given program (e.g. an episode
            number when the guide didn't send one) just contributes nothing, no blank line.
          </Text>
          <Group grow align="flex-start">
            <EpgLayoutBuilder
              label="Title"
              value={(pending["epg_title_layout"] as string) ?? (titleLayoutField.value as string) ?? ""}
              onChange={(v) => setField("epg_title_layout", v)}
            />
            <EpgLayoutBuilder
              label="Description"
              value={(pending["epg_description_layout"] as string) ?? (descriptionLayoutField.value as string) ?? ""}
              onChange={(v) => setField("epg_description_layout", v)}
            />
          </Group>
        </Paper>
      )}

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
  onChange,
}: {
  field: SettingField;
  pendingValue: string | number | undefined;
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

function parseEpgLayout(value: string): string[] {
  return value
    .split(",")
    .map((t) => t.trim().toLowerCase())
    .filter((t) => t in EPG_LAYOUT_TOKEN_LABELS);
}

// Builds one ordered list (Title or Description) of EPG layout tokens - move up/down to reorder,
// the x to drop one, and the picker at the bottom to add anything not already in the list
// (next_line is the one exception: it's a separator, not a field, so it can be added as many
// times as wanted).
function EpgLayoutBuilder({ label, value, onChange }: { label: string; value: string; onChange: (v: string) => void }) {
  const tokens = parseEpgLayout(value);

  function set(next: string[]) {
    onChange(next.join(","));
  }
  function move(index: number, delta: number) {
    const target = index + delta;
    if (target < 0 || target >= tokens.length) return;
    const next = [...tokens];
    [next[index], next[target]] = [next[target], next[index]];
    set(next);
  }
  function remove(index: number) {
    set(tokens.filter((_, i) => i !== index));
  }
  function add(token: string) {
    set([...tokens, token]);
  }

  const usedNonSeparator = new Set(tokens.filter((t) => t !== "next_line"));
  const addableTokens = EPG_LAYOUT_TOKENS.filter((t) => t === "next_line" || !usedNonSeparator.has(t));

  return (
    <Stack gap={4}>
      <Text size="sm" fw={600}>
        {label}
      </Text>
      <Stack gap={4}>
        {tokens.length === 0 && (
          <Text size="xs" c="dimmed">
            Empty - add at least one field below.
          </Text>
        )}
        {tokens.map((token, i) => (
          <Group key={i} gap={6} wrap="nowrap" p={6} style={{ border: "1px solid var(--mantine-color-default-border)", borderRadius: 6 }}>
            <Text size="xs" style={{ flex: 1 }} c={token === "next_line" ? "dimmed" : undefined} fs={token === "next_line" ? "italic" : undefined}>
              {EPG_LAYOUT_TOKEN_LABELS[token]}
            </Text>
            <ActionIcon size="sm" variant="subtle" disabled={i === 0} onClick={() => move(i, -1)}>
              <IconArrowUp size={14} />
            </ActionIcon>
            <ActionIcon size="sm" variant="subtle" disabled={i === tokens.length - 1} onClick={() => move(i, 1)}>
              <IconArrowDown size={14} />
            </ActionIcon>
            <ActionIcon size="sm" variant="subtle" color="red" onClick={() => remove(i)}>
              <IconX size={14} />
            </ActionIcon>
          </Group>
        ))}
      </Stack>
      <Select
        size="xs"
        placeholder="Add a field..."
        leftSection={<IconPlus size={14} />}
        data={addableTokens.map((t) => ({ value: t, label: EPG_LAYOUT_TOKEN_LABELS[t] }))}
        value={null}
        onChange={(v) => v && add(v)}
        disabled={addableTokens.length === 0}
        searchable
      />
    </Stack>
  );
}
