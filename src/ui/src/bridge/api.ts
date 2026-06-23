import type { PywebviewApi } from "../types/bridge";

export const apiReady: Promise<PywebviewApi> = new Promise((resolve) => {
  if (window.pywebview?.api) {
    resolve(window.pywebview.api);
    return;
  }
  window.addEventListener(
    "pywebviewready",
    () => {
      resolve(window.pywebview.api);
    },
    { once: true },
  );
});

export async function api(): Promise<PywebviewApi> {
  return apiReady;
}
