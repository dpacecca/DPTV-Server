import { Fragment, useState } from "react";
import { Badge, Button, Collapse, Group, Paper, Stack, Switch, Table, Text, TextInput, Title } from "@mantine/core";
import { IconChevronDown, IconChevronRight, IconSearch } from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useParams } from "react-router-dom";
import { useDebounce } from "use-debounce";
import { api } from "../api/client";
import type { PaginatedSourceChannels, Source, SourceCategory } from "../api/types";
import { EmptyState } from "../App";

const PAGE_SIZE = 200;

// A source category can hold tens of thousands of channels straight from a provider catalog.
// This fetches (and renders) one page at a time instead of dumping everything into the DOM -
// the browser resource concern this whole feature exists to address.
function CategoryChannels({ categoryId }: { categoryId: number }) {
  const [search, setSearch] = useState("");
  const [debouncedSearch] = useDebounce(search, 300);
  const [loadedPages, setLoadedPages] = useState(1);

  const { data, isLoading } = useQuery<PaginatedSourceChannels>({
    queryKey: ["source-channels", categoryId, debouncedSearch, loadedPages],
    queryFn: () =>
      api
        .get(`/api/sources/categories/${categoryId}/channels`, {
          params: { q: debouncedSearch || undefined, limit: PAGE_SIZE * loadedPages },
        })
        .then((r) => r.data),
  });

  if (isLoading && !data) return <Text size="sm" c="dimmed" p="sm">Loading...</Text>;

  return (
    <Stack gap="xs" p="xs">
      <TextInput
        size="xs"
        placeholder="Search channels..."
        leftSection={<IconSearch size={14} />}
        value={search}
        onChange={(e) => {
          setSearch(e.currentTarget.value);
          setLoadedPages(1);
        }}
        w={260}
      />
      {data?.total === 0 && <Text size="sm" c="dimmed">No channels</Text>}
      {data && data.total > 0 && (
        <>
          <Table fz="xs" verticalSpacing={4}>
            <Table.Tbody>
              {data.items.map((c) => (
                <Table.Tr key={c.id}>
                  <Table.Td>{c.name}</Table.Td>
                  <Table.Td>
                    {c.removed_at ? <Badge color="red" size="xs">removed by provider</Badge> : null}
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
          <Group justify="space-between">
            <Text size="xs" c="dimmed">
              Showing {data.items.length} of {data.total}
            </Text>
            {data.items.length < data.total && (
              <Button size="xs" variant="subtle" onClick={() => setLoadedPages((n) => n + 1)}>
                Load more
              </Button>
            )}
          </Group>
        </>
      )}
    </Stack>
  );
}

export default function SourceDetailPage() {
  const { sourceId } = useParams();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [expanded, setExpanded] = useState<number | null>(null);

  const { data: source } = useQuery<Source>({
    queryKey: ["sources", sourceId],
    queryFn: () => api.get(`/api/sources/${sourceId}`).then((r) => r.data),
  });

  const { data: categories, isLoading } = useQuery<SourceCategory[]>({
    queryKey: ["source-categories", sourceId],
    queryFn: () => api.get(`/api/sources/${sourceId}/categories`).then((r) => r.data),
  });

  const toggleMutation = useMutation({
    mutationFn: ({ id, enabled }: { id: number; enabled: boolean }) =>
      api.patch(`/api/sources/categories/${id}`, null, { params: { enabled } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["source-categories", sourceId] }),
  });

  return (
    <Stack>
      <Group justify="space-between">
        <div>
          <Button variant="subtle" size="xs" onClick={() => navigate("/sources")} mb={4}>
            &larr; Back to sources
          </Button>
          <Title order={3}>{source?.name}</Title>
          <Text c="dimmed" size="sm">
            Enable the categories you want available to import into playlists.
          </Text>
        </div>
      </Group>

      <Paper withBorder p="md">
        {!isLoading && categories?.length === 0 && (
          <EmptyState text="No categories loaded yet. Go back and click the sync icon to load categories/channels." />
        )}
        {categories && categories.length > 0 && (
          <Table striped>
            <Table.Thead>
              <Table.Tr>
                <Table.Th />
                <Table.Th>Category</Table.Th>
                <Table.Th>Type</Table.Th>
                <Table.Th>Channels</Table.Th>
                <Table.Th>Enabled</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {categories.map((c) => (
                <Fragment key={c.id}>
                  <Table.Tr>
                    <Table.Td style={{ width: 30, cursor: "pointer" }} onClick={() => setExpanded(expanded === c.id ? null : c.id)}>
                      {expanded === c.id ? <IconChevronDown size={16} /> : <IconChevronRight size={16} />}
                    </Table.Td>
                    <Table.Td>{c.name}</Table.Td>
                    <Table.Td>
                      <Badge variant="light">{c.channel_type}</Badge>
                    </Table.Td>
                    <Table.Td>{c.channel_count}</Table.Td>
                    <Table.Td>
                      <Switch
                        checked={c.enabled}
                        onChange={(e) => toggleMutation.mutate({ id: c.id, enabled: e.currentTarget.checked })}
                      />
                    </Table.Td>
                  </Table.Tr>
                  {expanded === c.id && (
                    <Table.Tr>
                      <Table.Td colSpan={5} p={0}>
                        <Collapse expanded>
                          <CategoryChannels categoryId={c.id} />
                        </Collapse>
                      </Table.Td>
                    </Table.Tr>
                  )}
                </Fragment>
              ))}
            </Table.Tbody>
          </Table>
        )}
      </Paper>
    </Stack>
  );
}
