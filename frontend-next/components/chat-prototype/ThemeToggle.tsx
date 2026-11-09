"use client";

// PROTOTYPE — throwaway.

export function ThemeToggle({
  theme,
  onToggle,
  className,
}: {
  theme: "light" | "dark";
  onToggle: () => void;
  className?: string;
}) {
  return (
    <button type="button" className={className ?? "chat-proto__theme-toggle"} onClick={onToggle}>
      {theme === "dark" ? "🌙 야간모드" : "☀️ 주간모드"}
    </button>
  );
}
