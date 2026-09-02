import {
  ConflictException,
  Injectable,
  InternalServerErrorException,
} from '@nestjs/common';
import { SupabaseService } from '../supabase/supabase.service';
import { PythonApiError, PythonApiService } from '../python/python-api.service';
import { PythonDomain } from '../python/python-api.types';
import { UserCompanyRecord, UserRecord } from './user.entity';

const TABLE = 'users';
const COMPANIES_TABLE = 'user_companies';

@Injectable()
export class UsersService {
  constructor(
    private readonly supabase: SupabaseService,
    private readonly python: PythonApiService,
  ) {}

  async companySlugFor(
    user: { id: string; name: string },
    domain: PythonDomain,
  ): Promise<string> {
    const existing = await this.findCompany(user.id, domain);
    if (existing) {
      const stillExists = await this.python.getCompany(existing.company_slug);
      if (stillExists) return existing.company_slug;

      await this.createCompany(existing.company_slug, user, domain, true);
      return existing.company_slug;
    }

    const slug = generateCompanySlug(user.id, domain);

    const hasAnyCompany = await this.hasAnyCompany(user.id);

    const known = await this.python.getCompany(slug);
    if (!known) {
      await this.createCompany(
        slug,
        user,
        domain,
        hasAnyCompany,
      );
    }

    const { error } = await this.supabase.client
      .from(COMPANIES_TABLE)
      .insert({ user_id: user.id, domain, company_slug: slug });

    if (error) {
      throw new InternalServerErrorException(
        'Failed to record the analysis workspace for this user',
      );
    }

    return slug;
  }

  private async hasAnyCompany(userId: string): Promise<boolean> {
    const { data, error } = await this.supabase.client
      .from(COMPANIES_TABLE)
      .select('company_slug')
      .eq('user_id', userId)
      .limit(1)
      .maybeSingle();

    if (error) {
      throw new InternalServerErrorException(
        'Failed to look up the analysis workspaces for this user',
      );
    }

    return data !== null;
  }

  private async createCompany(
    slug: string,
    user: { id: string; name: string },
    domain: PythonDomain,
    skipClaim: boolean,
  ): Promise<void> {
    try {
      await this.python.createCompany({
        companyId: slug,
        displayName: user.name || slug,
        domain,
        supabaseUserIds: skipClaim ? [] : [user.id],
      });
    } catch (err) {
      const alreadyClaimed =
        err instanceof PythonApiError &&
        err.status === 422 &&
        err.message.includes(user.id);
      if (!alreadyClaimed) throw err;

      await this.python.createCompany({
        companyId: slug,
        displayName: user.name || slug,
        domain,
        supabaseUserIds: [],
      });
    }
  }

  private async findCompany(
    userId: string,
    domain: PythonDomain,
  ): Promise<UserCompanyRecord | null> {
    const { data, error } = await this.supabase.client
      .from(COMPANIES_TABLE)
      .select('*')
      .eq('user_id', userId)
      .eq('domain', domain)
      .maybeSingle();

    if (error) {
      throw new InternalServerErrorException(
        'Failed to look up the analysis workspace for this user',
      );
    }

    return data as UserCompanyRecord | null;
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

export function generateCompanySlug(
  userId: string,
  domain: PythonDomain,
): string {
  const cleaned = userId.toLowerCase().replace(/[^a-z0-9]/g, '');
  return `u-${cleaned.slice(0, 8)}-${domain}`;
}
