export interface UserRecord {
  id: string;
  email: string;
  password_hash: string;
  name: string;
  company_slug: string | null;
  created_at: string;
}

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
