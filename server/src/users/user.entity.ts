export interface UserRecord {
  id: string;
  email: string;
  password_hash: string;
  name: string;
  /**
   * Superseded by `user_companies` (one Python tenant per (user, domain), not
   * per user) — kept only so the column need not be dropped; never written or
   * read by current code. See `UsersService.companySlugFor`.
   */
  company_slug: string | null;
  created_at: string;
}

/** One row of `user_companies`: the Python tenant for one (user, domain) pair. */
export interface UserCompanyRecord {
  user_id: string;
  domain: string;
  company_slug: string;
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
