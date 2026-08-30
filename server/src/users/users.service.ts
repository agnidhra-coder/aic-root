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

  /**
   * The user's Python tenant for one domain, created on first upload of that
   * domain.
   *
   * One company per (user, domain): a Retail upload and a Supply Chain upload
   * from the same user get separate companies, each seeded from the template
   * matching its own domain. The slug is derived deterministically from the
   * user id and domain together, so a half-finished provisioning (company
   * created, row not yet written) reconciles on the next attempt rather than
   * orphaning a tenant.
   */
  async companySlugFor(
    user: { id: string; name: string },
    domain: PythonDomain,
  ): Promise<string> {
    const existing = await this.findCompany(user.id, domain);
    if (existing) return existing.company_slug;

    const slug = generateCompanySlug(user.id, domain);

    // Python's own registry enforces one `supabase_user_id` per company,
    // globally -- a rule from when the design assumed one company per user
    // total, never per domain. NestJS is what now supports several domains
    // per user (via this table), so the id can only ever be claimed once:
    // on this user's first company here, or (for a tenant provisioned
    // before this table existed, or through some other path) on a company
    // Python already knows about that this table has never heard of.
    // Python's `external_ids` therefore only ever resolves a user to one
    // company, which is fine -- this table is what NestJS actually reads to
    // resolve (user, domain) -> slug, never Python's.
    const hasAnyCompany = await this.hasAnyCompany(user.id);

    // The company may already exist from an interrupted previous attempt for
    // this same (user, domain); creating it again would be a 409 on an id
    // this pair already owns.
    const known = await this.python.getCompany(slug);
    if (!known) {
      await this.createCompany(
        slug,
        user,
        domain,
        // Skip the claim outright when this table already knows the user
        // has a company -- saves the round trip that would just 422.
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

  /**
   * `POST /companies`, tolerating Python's one-id-per-company rule.
   *
   * `skipClaim` avoids the round trip when this table already knows the
   * user has a company elsewhere. But that table can be wrong in the other
   * direction — a tenant created before it existed, or by some other path —
   * so a 422 naming this exact id is caught and retried without the claim
   * rather than surfaced as a real provisioning failure. Any other error
   * (an unknown template, a duplicate slug) is a real failure and still
   * propagates.
   */
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

/**
 * `u-<first 8 chars of the uuid>-<domain>` — short, url-safe, and
 * deterministic in the (user id, domain) pair, so two domains for the same
 * user never collide on one slug. Satisfies the engine's `CompanySlug`
 * pattern, `^[a-z0-9][a-z0-9_-]{1,62}$`, which a lowercased uuid prefix and a
 * hyphenated domain name always do.
 */
export function generateCompanySlug(
  userId: string,
  domain: PythonDomain,
): string {
  const cleaned = userId.toLowerCase().replace(/[^a-z0-9]/g, '');
  return `u-${cleaned.slice(0, 8)}-${domain}`;
}
