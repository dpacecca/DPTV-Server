import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { Button, MantineProvider, createTheme, type MantineColorsTuple } from "@mantine/core";
import { Notifications } from "@mantine/notifications";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter } from "react-router-dom";

import "@mantine/core/styles.css";
import "@mantine/notifications/styles.css";
import "@mantine/dates/styles.css";

import App from "./App";
import { AuthProvider } from "./auth/AuthContext";

import "./theme.css";

// The DPTV family's look (DPTV-Player's StageTokens palette, Barlow Condensed titles, IBM Plex Mono
// numbers), with DPTV-Server's own accent: light blue, as Player is green and Recorder amber.
const sky: MantineColorsTuple = [
  "#e8f6ff", "#d2ecff", "#a6d9ff", "#78c6ff", "#6ccbff", "#4fb6f5", "#2e9fe6", "#1f86c7", "#1670a8", "#0c5a8a",
];
// Surfaces tinted towards the accent, as Player's are towards green: text (0) to page (8).
const dark: MantineColorsTuple = [
  "#e8f1f8", "#c7d3dd", "#a3b5c4", "#7d93a5", "#2b3a4a", "#223040", "#18212b", "#111820", "#0b1015", "#070a0e",
];

const theme = createTheme({
  primaryColor: "sky",
  primaryShade: { light: 7, dark: 4 },
  autoContrast: true,
  colors: { sky, dark },
  defaultRadius: "md",
  fontFamily: "Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
  fontFamilyMonospace: "'IBM Plex Mono', ui-monospace, SFMono-Regular, monospace",
  components: {
    // Dark ink on the light-blue fill, as DPTV-Player's accent buttons have.
    Button: Button.extend({
      vars: (_theme, props) =>
        (!props.color || props.color === "sky") && (props.variant ?? "filled") === "filled"
          ? { root: { "--button-color": "var(--dptv-accent-ink)" } }
          : { root: {} },
    }),
  },
  headings: {
    fontFamily: "'Barlow Condensed', Oswald, system-ui, sans-serif",
    fontWeight: "600",
  },
});

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <MantineProvider theme={theme} defaultColorScheme="dark">
      <Notifications position="top-right" />
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <AuthProvider>
            <App />
          </AuthProvider>
        </BrowserRouter>
      </QueryClientProvider>
    </MantineProvider>
  </StrictMode>
);
