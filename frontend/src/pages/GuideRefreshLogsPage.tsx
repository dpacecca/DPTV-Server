import { useEffect, useRef, useState } from "react";
import { Badge, Button, Group, Paper, ScrollArea, Select, Stack, Switch, Text, Title } from "@mantine/core";
import { IconRefresh } from "@tabler/icons-react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";

const LINE_OPTIONS = ["50", "100", "200", "500", "1000"];

export default function GuideRefreshLogsPage() {
  const [lines, setLines] = useState("100");
  const [live, setLive] = useState(true);
  const scrollViewportRef = useRef<HTMLDivElement>(null);
  // Only auto-scroll to the newest lines while the admin is already at the bottom - otherwise a
  // live-updating page would keep yanking them away from whatever older line they scrolled up to read.
  const wasAtBottomRef = useRef(true);

  const { data, isFetching, isError, refetch } = useQuery<{ lines: string[] }>({
    queryKey: ["guide-refresh-logs", lines],
    queryFn: () => api.get("/api/logs/guide-refresh", { params: { lines: Number(lines) } }).then((r) => r.data),
    refetchInterval: live ? 2000 : false,
  });

  useEffect(() => {
    const el = scrollViewportRef.current;
    if (el && wasAtBottomRef.current) {
      el.scrollTop = el.scrollHeight;
    }
  }, [data]);

  function handleScrollPositionChange() {
    const el = scrollViewportRef.current;
    if (!el) return;
    wasAtBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
  }

  return (
    <Stack gap="sm">
      <Group justify="space-between" wrap="wrap">
        <Group gap="xs">
          <Title order={3}>Guide Refresh Logs</Title>
          {isError && (
            <Badge color="red" variant="light">
              Disconnected - retrying...
            </Badge>
          )}
        </Group>
        <Group gap="xs">
          <Select data={LINE_OPTIONS} value={lines} onChange={(v) => setLines(v || "100")} w={90} allowDeselect={false} />
          <Switch label="Live updates" checked={live} onChange={(e) => setLive(e.currentTarget.checked)} />
          <Button size="xs" variant="light" leftSection={<IconRefresh size={14} />} loading={isFetching} onClick={() => refetch()}>
            Refresh
          </Button>
        </Group>
      </Group>

      <Text size="xs" c="dimmed">
        Scheduled and manually-triggered EPG source refresh activity, including the iptv-org
        scraper's own per-batch/per-channel scrape progress.
      </Text>

      <Paper withBorder style={{ overflow: "hidden" }}>
        <ScrollArea h="calc(100vh - 240px)" viewportRef={scrollViewportRef} onScrollPositionChange={handleScrollPositionChange}>
          <Text component="pre" size="xs" ff="monospace" p="sm" style={{ whiteSpace: "pre-wrap", wordBreak: "break-all", margin: 0 }}>
            {data?.lines?.length ? data.lines.join("\n") : "No guide refresh activity logged yet."}
          </Text>
        </ScrollArea>
      </Paper>
    </Stack>
  );
}
