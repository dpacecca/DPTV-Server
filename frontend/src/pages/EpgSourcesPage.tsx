import { useMemo, useState } from "react";
import {
  ActionIcon,
  Alert,
  Badge,
  Button,
  Group,
  Modal,
  MultiSelect,
  NumberInput,
  Paper,
  SegmentedControl,
  Stack,
  Table,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconAlertCircle, IconPlus, IconRefresh, IconTrash } from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { EpgSource, IptvOrgCatalog } from "../api/types";
import { EmptyState } from "../App";

type SourceKind = "url" | "iptv_org";
type IptvOrgMode = "country" | "category";

export default function EpgSourcesPage() {
  const qc = useQueryClient();
  const [modalOpen, setModalOpen] = useState(false);
  const [name, setName] = useState("");
  const [sourceKind, setSourceKind] = useState<SourceKind>("url");
  const [url, setUrl] = useState("");
  const [iptvOrgMode, setIptvOrgMode] = useState<IptvOrgMode>("country");
  const [selectedValues, setSelectedValues] = useState<string[]>([]);
  const [refreshInterval, setRefreshInterval] = useState(720);

  const { data: sources, isLoading } = useQuery<EpgSource[]>({
    queryKey: ["epg-sources"],
    queryFn: () => api.get("/api/epg-sources").then((r) => r.data),
  });

  const { data: catalog, isLoading: catalogLoading } = useQuery<IptvOrgCatalog>({
    queryKey: ["epg-sources", "iptv-org-catalog"],
    queryFn: () => api.get("/api/epg-sources/iptv-org/catalog").then((r) => r.data),
    enabled: modalOpen && sourceKind === "iptv_org",
  });

  const catalogOptions = useMemo(() => {
    if (!catalog) return [];
    if (iptvOrgMode === "country") {
      return catalog.countries.map((c) => ({ value: c.name, label: `${c.name} (${c.channel_count})` }));
    }
    return catalog.categories.map((c) => ({ value: c.id, label: `${c.name} (${c.channel_count})` }));
  }, [catalog, iptvOrgMode]);

  const resetForm = () => {
    setName("");
    setSourceKind("url");
    setUrl("");
    setIptvOrgMode("country");
    setSelectedValues([]);
    setRefreshInterval(720);
  };

  const createMutation = useMutation({
    mutationFn: () =>
      api.post("/api/epg-sources", {
        name,
        source_kind: sourceKind,
        url: sourceKind === "url" ? url : undefined,
        iptv_org_selection: sourceKind === "iptv_org" ? { mode: iptvOrgMode, values: selectedValues } : undefined,
        refresh_interval_minutes: refreshInterval,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["epg-sources"] });
      setModalOpen(false);
      resetForm();
    },
    onError: () => notifications.show({ message: "Failed to create EPG source", color: "red" }),
  });

  const refreshMutation = useMutation({
    mutationFn: (id: number) => api.post(`/api/epg-sources/${id}/refresh`),
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: ["epg-sources"] });
      notifications.show({ message: `Loaded ${res.data.channels} channels, ${res.data.programs} programs`, color: "green" });
    },
    onError: (err: any) =>
      notifications.show({ message: err?.response?.data?.detail || "Refresh failed", color: "red" }),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.delete(`/api/epg-sources/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["epg-sources"] }),
  });

  const canSave =
    !!name && (sourceKind === "url" ? !!url : selectedValues.length > 0 && (catalog?.available ?? false));

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
                <Table.Th>Kind</Table.Th>
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
                  <Table.Td>
                    {s.source_kind === "iptv_org" ? (
                      <Badge variant="light" color="grape">
                        iptv-org: {s.iptv_org_selection?.mode} ({s.iptv_org_selection?.values.length ?? 0})
                      </Badge>
                    ) : (
                      <Badge variant="light">URL</Badge>
                    )}
                  </Table.Td>
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

      <Modal
        opened={modalOpen}
        onClose={() => {
          setModalOpen(false);
          resetForm();
        }}
        title="Add EPG Source"
        size="lg"
      >
        <Stack>
          <SegmentedControl
            fullWidth
            value={sourceKind}
            onChange={(v) => {
              setSourceKind(v as SourceKind);
              setSelectedValues([]);
            }}
            data={[
              { label: "XMLTV URL", value: "url" },
              { label: "iptv-org/epg (scrape)", value: "iptv_org" },
            ]}
          />

          <TextInput label="Name" value={name} onChange={(e) => setName(e.currentTarget.value)} required />

          {sourceKind === "url" && (
            <TextInput
              label="XMLTV URL"
              description="Plain .xml or gzip-compressed .xml.gz both work"
              value={url}
              onChange={(e) => setUrl(e.currentTarget.value)}
              required
            />
          )}

          {sourceKind === "iptv_org" && (
            <>
              {!catalogLoading && catalog && !catalog.available && (
                <Alert icon={<IconAlertCircle size={16} />} color="yellow" title="Not configured">
                  This server doesn't have the iptv-org/epg scraper set up yet. An admin needs to install Node.js,
                  clone{" "}
                  <Text span ff="monospace" size="sm">
                    github.com/iptv-org/epg
                  </Text>
                  , run <Text span ff="monospace" size="sm">npm install</Text>, and set{" "}
                  <Text span ff="monospace" size="sm">DPTV_IPTV_ORG_EPG_DIR</Text> to the checkout path. See the README.
                </Alert>
              )}
              {(catalogLoading || catalog?.available) && (
                <>
                  <SegmentedControl
                    fullWidth
                    value={iptvOrgMode}
                    onChange={(v) => {
                      setIptvOrgMode(v as IptvOrgMode);
                      setSelectedValues([]);
                    }}
                    data={[
                      { label: "By country", value: "country" },
                      { label: "By category", value: "category" },
                    ]}
                  />
                  <MultiSelect
                    label={iptvOrgMode === "country" ? "Countries" : "Categories"}
                    placeholder="Search and select..."
                    searchable
                    limit={50}
                    data={catalogOptions}
                    value={selectedValues}
                    onChange={setSelectedValues}
                    disabled={catalogLoading}
                    description={
                      iptvOrgMode === "category"
                        ? "Only channels with real category metadata are eligible - counts reflect actual scrapable channels."
                        : "Channel counts include a TLD-based estimate where iptv-org has no per-channel country data."
                    }
                  />
                </>
              )}
            </>
          )}

          <NumberInput
            label="Refresh interval (minutes)"
            description={sourceKind === "iptv_org" ? "Scraping many sites can take a while - avoid very frequent refreshes." : undefined}
            value={refreshInterval}
            onChange={(v) => setRefreshInterval(Number(v) || 720)}
            min={15}
          />

          <Button onClick={() => createMutation.mutate()} loading={createMutation.isPending} disabled={!canSave}>
            Save
          </Button>
        </Stack>
      </Modal>
    </Stack>
  );
}
