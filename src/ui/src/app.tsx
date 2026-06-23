import { useEffect } from "preact/hooks";
import { Toast } from "./components/Toast";
import { bootDesktopController } from "./controller/desktopController";
import { ConsoleView } from "./features/console/ConsoleView";
import { Header } from "./features/chrome/Header";
import { EngineBars } from "./features/engine/EngineBars";
import { SearchTabs } from "./features/search/SearchTabs";
import { SettingsView } from "./features/settings/SettingsView";
import { SpeakersView } from "./features/speakers/SpeakersView";
import { TranscriptView } from "./features/transcript/TranscriptView";

export function App() {
  useEffect(() => {
    bootDesktopController();
  }, []);

  return (
    <>
      <Header />
      <EngineBars />
      <SearchTabs />
      <main>
        <TranscriptView />
        <SpeakersView />
        <SettingsView />
        <Toast />
        <ConsoleView />
      </main>
    </>
  );
}
