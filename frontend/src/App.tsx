import { AppShell, Badge, Burger, Group, NavLink, Text, Title, Tooltip, Button } from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import {
  IconAntenna,
  IconCalendarTime,
  IconClipboardText,
  IconFileText,
  IconListDetails,
  IconLogout,
  IconPlaylist,
  IconUsers,
} from "@tabler/icons-react";
import { useQuery } from "@tanstack/react-query";
import { Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";

import { api } from "./api/client";
import type { VersionStatus } from "./api/types";
import { useAuth } from "./auth/AuthContext";
import LoginPage from "./pages/LoginPage";
import SourcesPage from "./pages/SourcesPage";
import SourceDetailPage from "./pages/SourceDetailPage";
import EpgSourcesPage from "./pages/EpgSourcesPage";
import PlaylistsPage from "./pages/PlaylistsPage";
import PlaylistEditorPage from "./pages/PlaylistEditorPage";
import XcUsersPage from "./pages/XcUsersPage";
import SchedulerPage from "./pages/SchedulerPage";
import XcLogsPage from "./pages/XcLogsPage";
import GuideRefreshLogsPage from "./pages/GuideRefreshLogsPage";

const NAV_ITEMS = [
  { to: "/sources", label: "Sources", icon: IconAntenna },
  { to: "/epg-sources", label: "EPG Sources", icon: IconListDetails },
  { to: "/playlists", label: "Playlists", icon: IconPlaylist },
  { to: "/xc-users", label: "XC Users", icon: IconUsers },
  { to: "/scheduler", label: "Scheduler", icon: IconCalendarTime },
  { to: "/xc-logs", label: "XC Server Logs", icon: IconFileText },
  { to: "/guide-refresh-logs", label: "Guide Refresh Logs", icon: IconClipboardText },
];

function VersionBadge() {
  // GitHub's own response is what's cached server-side (see app/services/version.py) - this
  // client-side staleTime just avoids re-fetching on every nav within the app, not the actual
  // rate-limit protection.
  const { data } = useQuery<VersionStatus>({
    queryKey: ["version"],
    queryFn: () => api.get("/api/version").then((r) => r.data),
    staleTime: 5 * 60 * 1000,
  });
  if (!data) return null;

  return (
    <Group gap={6}>
      <Text size="xs" c="dimmed">
        v{data.version}
        {data.commit && ` build ${data.commit}`}
      </Text>
      {data.up_to_date === true && (
        <Badge size="xs" color="green" variant="light">
          Up to date
        </Badge>
      )}
      {data.up_to_date === false && (
        <Tooltip label={`main is at ${data.latest_commit}`}>
          <Badge
            size="xs"
            color="orange"
            variant="light"
            component="a"
            href="https://github.com/dpacecca/DPTV-Server/commits/main"
            target="_blank"
            rel="noopener noreferrer"
            style={{ cursor: "pointer" }}
          >
            Update available
          </Badge>
        </Tooltip>
      )}
    </Group>
  );
}

function Shell() {
  const [opened, { toggle }] = useDisclosure();
  const location = useLocation();
  const navigate = useNavigate();
  const { logout } = useAuth();

  return (
    <AppShell header={{ height: 56 }} navbar={{ width: 220, breakpoint: "sm", collapsed: { mobile: !opened } }} padding="md">
      <AppShell.Header>
        <Group h="100%" px="md" justify="space-between">
          <Group>
            <Burger opened={opened} onClick={toggle} hiddenFrom="sm" size="sm" />
            <Title order={4}>DPTV-Server</Title>
            <VersionBadge />
          </Group>
          <Button variant="subtle" size="xs" leftSection={<IconLogout size={14} />} onClick={logout}>
            Log out
          </Button>
        </Group>
      </AppShell.Header>
      <AppShell.Navbar p="sm">
        {NAV_ITEMS.map((item) => (
          <NavLink
            key={item.to}
            label={item.label}
            leftSection={<item.icon size={18} />}
            active={location.pathname.startsWith(item.to)}
            onClick={() => navigate(item.to)}
          />
        ))}
      </AppShell.Navbar>
      <AppShell.Main>
        <Routes>
          <Route path="/sources" element={<SourcesPage />} />
          <Route path="/sources/:sourceId" element={<SourceDetailPage />} />
          <Route path="/epg-sources" element={<EpgSourcesPage />} />
          <Route path="/playlists" element={<PlaylistsPage />} />
          <Route path="/playlists/:playlistId" element={<PlaylistEditorPage />} />
          <Route path="/xc-users" element={<XcUsersPage />} />
          <Route path="/scheduler" element={<SchedulerPage />} />
          <Route path="/xc-logs" element={<XcLogsPage />} />
          <Route path="/guide-refresh-logs" element={<GuideRefreshLogsPage />} />
          <Route path="*" element={<Navigate to="/playlists" replace />} />
        </Routes>
      </AppShell.Main>
    </AppShell>
  );
}

export default function App() {
  const { isAuthenticated } = useAuth();

  if (!isAuthenticated) {
    return (
      <Routes>
        <Route path="*" element={<LoginPage />} />
      </Routes>
    );
  }

  return <Shell />;
}

export function EmptyState({ text }: { text: string }) {
  return (
    <Text c="dimmed" ta="center" py="xl">
      {text}
    </Text>
  );
}
