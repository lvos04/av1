export function cn(...classes: Array<string | false | null | undefined>): string {
  return classes.filter(Boolean).join(" ");
}

export function bytes(value?: number | null): string {
  if (value === null || value === undefined) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let result = value;
  let index = 0;
  while (Math.abs(result) >= 1024 && index < units.length - 1) {
    result /= 1024;
    index += 1;
  }
  return `${result.toLocaleString("nl-NL", { maximumFractionDigits: 1 })} ${units[index]}`;
}

export function duration(seconds?: number | null): string {
  if (seconds === null || seconds === undefined) return "—";
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const rest = Math.floor(seconds % 60);
  return hours > 0 ? `${hours}u ${minutes}m` : `${minutes}m ${rest}s`;
}

export function fileName(path?: string): string {
  return path?.split("/").pop() ?? "Geen actieve job";
}
