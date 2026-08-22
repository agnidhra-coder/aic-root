/**
 * One-off script to delete an uploaded file: its row in `uploads`, its
 * cascaded row in `analyses`, and its object in Supabase Storage.
 *
 * Edit FILENAME below, then run from server/:
 *   npx ts-node scripts/delete-upload.ts
 */
import 'dotenv/config';
import { createClient } from '@supabase/supabase-js';

const FILENAME = 'retail_kpi_data_large.csv'; // <-- change this each time

const BUCKET = 'kpi-uploads';

async function main() {
  const supabaseUrl = process.env.SUPABASE_URL;
  const supabaseSecretKey = process.env.SUPABASE_SECRET_KEY;

  if (!supabaseUrl || !supabaseSecretKey) {
    throw new Error('SUPABASE_URL / SUPABASE_SECRET_KEY missing from server/.env');
  }

  const supabase = createClient(supabaseUrl, supabaseSecretKey, {
    auth: { persistSession: false },
  });

  const { data: matches, error: findError } = await supabase
    .from('uploads')
    .select('id, filename, storage_path, created_at')
    .eq('filename', FILENAME);

  if (findError) {
    throw new Error(`Failed to look up upload: ${findError.message}`);
  }

  if (!matches || matches.length === 0) {
    console.log(`No upload found with filename "${FILENAME}".`);
    return;
  }

  if (matches.length > 1) {
    console.log(`Found ${matches.length} uploads named "${FILENAME}":`);
    matches.forEach((m) => console.log(`  - id=${m.id} created_at=${m.created_at}`));
    console.log('Deleting all of them.');
  }

  for (const upload of matches) {
    const { error: storageError } = await supabase.storage
      .from(BUCKET)
      .remove([upload.storage_path]);

    if (storageError) {
      console.warn(`  Warning: failed to remove storage object for ${upload.id}: ${storageError.message}`);
    }

    const { error: deleteError } = await supabase.from('uploads').delete().eq('id', upload.id);

    if (deleteError) {
      console.error(`  Failed to delete DB row for ${upload.id}: ${deleteError.message}`);
      continue;
    }

    console.log(`Deleted upload ${upload.id} ("${upload.filename}").`);
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
