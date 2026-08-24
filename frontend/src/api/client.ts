import axios from "axios";

export const API_BASE = import.meta.env.VITE_API_BASE_URL || "";

export const api = axios.create({ baseURL: API_BASE });

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
