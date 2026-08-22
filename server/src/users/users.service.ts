import {
  ConflictException,
  Injectable,
  InternalServerErrorException,
} from '@nestjs/common';
import { SupabaseService } from '../supabase/supabase.service';
import { UserRecord } from './user.entity';

const TABLE = 'users';

@Injectable()
export class UsersService {
  constructor(private readonly supabase: SupabaseService) {}

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
