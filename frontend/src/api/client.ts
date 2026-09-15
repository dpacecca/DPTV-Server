import axios from "axios";

export const API_BASE = import.meta.env.VITE_API_BASE_URL || "";

export const api = axios.create({
  baseURL: API_BASE,
  // Array-valued params (e.g. epg_source_ids=[1,2]) must serialize as repeated keys
  // (?epg_source_ids=1&epg_source_ids=2) to match FastAPI's `list[int]` query param parsing -
  // axios's default `indexes: false` instead produces bracket notation (`epg_source_ids[]=1`),
  // which FastAPI silently fails to bind (falls back to the default, e.g. "search everything"
  // instead of the selected filter).
  paramsSerializer: { indexes: null },
});

export function setToken(token: string | null) {
  if (token) {
    localStorage.setItem("dptv_token", token);
    api.defaults.headers.common.Authorization = `Bearer ${token}`;
  } else {
    localStorage.removeItem("dptv_token");
    delete api.defaults.headers.common.Authorization;
  }
}

const existing = localStorage.getItem("dptv_token");
if (existing) setToken(existing);

api.interceptors.response.use(
  (res) => res,
  (err) => {
    if (err?.response?.status === 401) {
      setToken(null);
      window.location.href = "/login";
    }
    return Promise.reject(err);
  }
);

interface EpgRefreshJobStatus {
  job_id: string;
  status: "running" | "done" | "error";
  error: string | null;
  result: unknown;
}

// EPG source refresh runs as a background job (scraping a slow broadcaster site can take many
// minutes, far longer than any reverse proxy/tunnel in front of this app would keep a request
// open) - the endpoints below just kick it off and return a job id, so callers poll this until
// it settles instead of awaiting one long HTTP request.
async function pollEpgRefreshJob(jobId: string): Promise<EpgRefreshJobStatus> {
  for (;;) {
    const { data } = await api.get<EpgRefreshJobStatus>(`/api/epg-sources/refresh-jobs/${jobId}`);
    if (data.status !== "running") return data;
    await new Promise((resolve) => setTimeout(resolve, 1500));
  }
}

export async function refreshEpgSource(epgSourceId: number): Promise<{ channels: number; programs: number }> {
  const { data } = await api.post(`/api/epg-sources/${epgSourceId}/refresh`);
  const job = await pollEpgRefreshJob(data.job_id);
  if (job.status === "error") throw new Error(job.error || "Refresh failed");
  return job.result as { channels: number; programs: number };
}

export async function refreshAllEpgSources(): Promise<{ epg_sources: Record<string, unknown>; errors: string[] }> {
  const { data } = await api.post("/api/epg-sources/refresh-all");
  const job = await pollEpgRefreshJob(data.job_id);
  if (job.status === "error") throw new Error(job.error || "Refresh failed");
  return job.result as { epg_sources: Record<string, unknown>; errors: string[] };
}
