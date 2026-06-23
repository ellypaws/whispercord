import { useEffect } from "preact/hooks";
import { apiReady } from "./bridge/api";

export function App() {
  useEffect(() => {
    void apiReady;
  }, []);

  return null;
}
