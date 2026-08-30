export interface UserRecord {
  id: string;
  email: string;
  password_hash: string;
  name: string;
  /**
   * The user's tenant in the Python KPI engine, created lazily on first upload.
   * Null until then. One company per user, derived from `id` and never reused.
   */
  company_slug: string | null;
  created_at: string;
}

export interface PublicUser {
  id: string;
  email: string;
  name: string;
}

export function toPublicUser(user: UserRecord): PublicUser {
  return { id: user.id, email: user.email, name: user.name };
}
