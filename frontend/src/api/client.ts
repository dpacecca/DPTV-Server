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
