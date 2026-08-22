const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? "";
const TOKEN_KEY = "fin-datapilot.access-token";

type AnonymousTokenResponse = { access_token: string };

const RENEW_BEFORE_SECONDS = 7 * 24 * 60 * 60;
let tokenRequest: Promise<string> | null = null;

function expiresSoon(token: string): boolean {
  const parts = token.split(".");
  if (parts.length !== 4) return true;
  const expiresAt = Number(parts[2]);
  return !Number.isFinite(expiresAt) || expiresAt - Date.now() / 1000 < RENEW_BEFORE_SECONDS;
}

async function issueToken(existing?: string): Promise<string> {
  const response = await fetch(`${API_BASE}/api/auth/anonymous`, {
    method: "POST",
    headers: existing ? { Authorization: `Bearer ${existing}` } : undefined,
  });
  if (!response.ok) throw new Error("Unable to initialize secure session");
  const body = (await response.json()) as AnonymousTokenResponse;
  localStorage.setItem(TOKEN_KEY, body.access_token);
  return body.access_token;
}

/**
 * Obtain a server-signed anonymous identity once per browser profile.
 * This is deliberately not a user-selected id: the API derives ownership
 * solely from this bearer token.
 */
export async function getAccessToken(): Promise<string> {
  const existing = localStorage.getItem(TOKEN_KEY);
  if (existing && !expiresSoon(existing)) return existing;
  // Several components bootstrap in parallel. Share one issuance request so
  // first load cannot accidentally create multiple anonymous identities.
  if (!tokenRequest) {
    tokenRequest = issueToken(existing ?? undefined).finally(() => {
      tokenRequest = null;
    });
  }
  return tokenRequest;
}

export async function authHeaders(): Promise<Record<string, string>> {
  return { Authorization: `Bearer ${await getAccessToken()}` };
}
