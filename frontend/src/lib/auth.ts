const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? "";
const TOKEN_KEY = "fin-datapilot.access-token";

type AnonymousTokenResponse = { access_token: string };

/**
 * Obtain a server-signed anonymous identity once per browser profile.
 * This is deliberately not a user-selected id: the API derives ownership
 * solely from this bearer token.
 */
export async function getAccessToken(): Promise<string> {
  const existing = localStorage.getItem(TOKEN_KEY);
  if (existing) return existing;
  const response = await fetch(`${API_BASE}/api/auth/anonymous`, { method: "POST" });
  if (!response.ok) throw new Error("Unable to initialize secure session");
  const body = (await response.json()) as AnonymousTokenResponse;
  localStorage.setItem(TOKEN_KEY, body.access_token);
  return body.access_token;
}

export async function authHeaders(): Promise<Record<string, string>> {
  return { Authorization: `Bearer ${await getAccessToken()}` };
}
