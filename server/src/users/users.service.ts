import {
  ConflictException,
  Injectable,
  InternalServerErrorException,
} from '@nestjs/common';
import { SupabaseService } from '../supabase/supabase.service';
import { PythonApiService } from '../python/python-api.service';
import { UserRecord } from './user.entity';

const TABLE = 'users';

@Injectable()
export class UsersService {
  constructor(
    private readonly supabase: SupabaseService,
    private readonly python: PythonApiService,
  ) {}

  /**
   * The user's Python tenant, created on first use.
   *
   * One company per user. The slug is derived deterministically from the user
   * id, so a half-finished provisioning (company created, column not yet
   * written) reconciles on the next attempt rather than orphaning a tenant.
   */
  async ensureCompanySlug(user: {
    id: string;
    name: string;
  }): Promise<string> {
    const existing = await this.findById(user.id);
    if (existing?.company_slug) return existing.company_slug;

    const slug = companySlugFor(user.id);

    // The company may already exist from an interrupted first upload; creating
    // it again would be a 409 on an id this user already owns.
    const known = await this.python.getCompany(slug);
    if (!known) {
      await this.python.createCompany({
        companyId: slug,
        displayName: user.name || slug,
        supabaseUserIds: [user.id],
      });
    }

    const { error } = await this.supabase.client
      .from(TABLE)
      .update({ company_slug: slug })
      .eq('id', user.id);

    if (error) {
      throw new InternalServerErrorException(
        'Failed to record the analysis workspace for this user',
      );
    }

    return slug;
  }

  async findByEmail(email: string): Promise<UserRecord | null> {
    const { data, error } = await this.supabase.client
      .from(TABLE)
      .select('*')
      .eq('email', email)
      .maybeSingle();

    if (error) {
      throw new InternalServerErrorException('Failed to look up user');
    }

    return data as UserRecord | null;
  }

  async findById(id: string): Promise<UserRecord | null> {
    const { data, error } = await this.supabase.client
      .from(TABLE)
      .select('*')
      .eq('id', id)
      .maybeSingle();

    if (error) {
      throw new InternalServerErrorException('Failed to look up user');
    }

    return data as UserRecord | null;
  }

  async create(params: {
    email: string;
    passwordHash: string;
    name: string;
  }): Promise<UserRecord> {
    const { data, error } = await this.supabase.client
      .from(TABLE)
      .insert({
        email: params.email,
        password_hash: params.passwordHash,
        name: params.name,
      })
      .select('*')
      .single();

    if (error) {
      if (error.code === '23505') {
        throw new ConflictException(
          'An account with this email already exists',
        );
      }
      throw new InternalServerErrorException('Failed to create user');
    }

    return data as UserRecord;
  }
}

/**
 * `u-<first 8 chars of the uuid>` — short, url-safe, and deterministic in the
 * user id. It must satisfy the engine's `CompanySlug` pattern,
 * `^[a-z0-9][a-z0-9_-]{1,62}$`, which a lowercased uuid prefix always does.
 */
export function companySlugFor(userId: string): string {
  const cleaned = userId.toLowerCase().replace(/[^a-z0-9]/g, '');
  return `u-${cleaned.slice(0, 8)}`;
}
