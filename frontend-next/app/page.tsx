import { AppShell } from "@/components/AppShell";
import { LangProvider } from "@/lib/i18n";

export default function Home() {
  return (
    <LangProvider>
      <AppShell />
    </LangProvider>
  );
}
