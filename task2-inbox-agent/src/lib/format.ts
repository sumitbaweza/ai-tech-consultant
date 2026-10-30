// Dates the way Gmail's list shows them: the time for today, "Oct 29" this year, else the date
export function formatListDate(date: Date, now: Date, timeZone = "UTC"): string {
  const day = (d: Date) => d.toLocaleDateString("en-US", { timeZone });
  if (day(date) === day(now)) {
    return date.toLocaleTimeString("en-US", { timeZone, hour: "numeric", minute: "2-digit" });
  }
  const year = (d: Date) => d.toLocaleDateString("en-US", { timeZone, year: "numeric" });
  if (year(date) === year(now)) {
    return date.toLocaleDateString("en-US", { timeZone, month: "short", day: "numeric" });
  }
  return date.toLocaleDateString("en-US", { timeZone, year: "numeric", month: "numeric", day: "numeric" });
}

// Up to two letters for the avatar: "Maria Lopez" -> "ML", or the email's first letter
export function initials(name: string | null | undefined, email: string | null | undefined): string {
  const words = (name ?? "").trim().split(/\s+/).filter((word) => word !== "");
  const letters = words.slice(0, 2).map((word) => word.charAt(0));
  if (letters.length > 0) return letters.join("").toUpperCase();
  return (email ?? "?").charAt(0).toUpperCase();
}

// "just now", "5 min ago", "3 h ago", "2 days ago": how long since the last sync
export function formatAgo(date: Date, now: Date): string {
  const minutes = Math.floor((now.getTime() - date.getTime()) / 60_000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${String(minutes)} min ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${String(hours)} h ago`;
  const days = Math.floor(hours / 24);
  return days === 1 ? "1 day ago" : `${String(days)} days ago`;
}
