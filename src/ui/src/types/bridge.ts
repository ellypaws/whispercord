import type { AppConfig } from "./config";

export interface HardwareInfo {
  vendor: "nvidia" | "amd" | "intel" | "cpu" | string;
  name: string;
  gfx: string | null;
  vulkan: boolean;
  recommended_engine: string;
  recommended_device: string;
}

export interface DiscordClient {
  folder: string;
  exe: string;
  port: number;
  live: boolean;
  running: boolean;
  detecting?: boolean;
}

export interface InputDevice {
  index: number;
  name: string;
}

export interface EnsureClientResult {
  port: number;
  status: string;
}

export interface BackendProgress {
  stage: string;
  label: string;
  pct: number | null;
  done: boolean;
  active: boolean;
}

export interface UpdateInfo {
  available: boolean;
  current: string;
  latest: string;
  url: string;
}

export interface ModelInfo {
  id?: string;
  name: string;
  cached?: boolean;
  size?: string | number;
  [key: string]: unknown;
}

export interface PywebviewApi {
  get_config(): Promise<AppConfig>;
  save_config(cfg: AppConfig): Promise<boolean>;
  setup_requested(): Promise<boolean>;
  self_identity(): Promise<string[]>;
  detect_hardware(): Promise<HardwareInfo>;
  list_clients(): Promise<DiscordClient[]>;
  list_clients_quick(): Promise<DiscordClient[]>;
  list_input_devices(): Promise<InputDevice[]>;
  ensure_client(folder: string, restart: boolean): Promise<EnsureClientResult>;
  start_backend(): Promise<boolean>;
  stop_backend(): Promise<boolean>;
  backend_status(): Promise<boolean>;
  relay_port(): Promise<number | null>;
  get_log(): Promise<string>;
  clear_log(): Promise<boolean>;
  get_progress(): Promise<BackendProgress>;
  cuda_status(): Promise<boolean>;
  app_version(): Promise<string>;
  check_update(): Promise<UpdateInfo>;
  open_url(url: string): Promise<boolean>;
  list_models(): Promise<ModelInfo[]>;
  model_cached(name: string): Promise<boolean>;
  delete_model(name: string): Promise<boolean>;
}

declare global {
  interface Window {
    pywebview: {
      api: PywebviewApi;
    };
  }
}
