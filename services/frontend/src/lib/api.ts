export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8010";

export function apiUrl(path: string): string {
  return `${API_BASE}${path}`;
}

export async function getJSON<T>(path: string): Promise<T> {
  const res = await fetch(apiUrl(path));
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json() as Promise<T>;
}
