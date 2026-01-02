/**
 * Migration script to convert JSON files to SQLite database
 * This script reads existing JSON files and migrates them to SQLite
 */

import { promises as fs } from 'fs';
import { join } from 'path';
import { getDb, createStudy, createFile, readStudies, readFiles } from '../src/lib/sqliteDb';
import { Study, UploadedFile } from '../src/lib/types';
import { Timestamp } from '../src/lib/compat';

const DATA_DIR = join(process.cwd(), 'data', 'db');
const STUDIES_JSON = join(DATA_DIR, 'studies.json');
const FILES_JSON = join(DATA_DIR, 'files.json');
const DB_PATH = join(DATA_DIR, 'database.db');

/**
 * Check if database already exists and has data
 */
async function databaseExists(): Promise<boolean> {
  try {
    await fs.access(DB_PATH);
    // Check if database has any studies
    const studies = readStudies();
    return studies.length > 0;
  } catch {
    return false;
  }
}

/**
 * Backup JSON files
 */
async function backupJsonFiles(): Promise<void> {
  const backupDir = join(DATA_DIR, 'backup');
  await fs.mkdir(backupDir, { recursive: true });
  
  const timestamp = Date.now();
  
  try {
    if (await fileExists(STUDIES_JSON)) {
      await fs.copyFile(STUDIES_JSON, join(backupDir, `studies.json.backup.${timestamp}`));
      console.log(`Backed up ${STUDIES_JSON} to backup directory`);
    }
  } catch (error) {
    console.warn('Failed to backup studies.json:', error);
  }
  
  try {
    if (await fileExists(FILES_JSON)) {
      await fs.copyFile(FILES_JSON, join(backupDir, `files.json.backup.${timestamp}`));
      console.log(`Backed up ${FILES_JSON} to backup directory`);
    }
  } catch (error) {
    console.warn('Failed to backup files.json:', error);
  }
}

/**
 * Check if file exists
 */
async function fileExists(path: string): Promise<boolean> {
  try {
    await fs.access(path);
    return true;
  } catch {
    return false;
  }
}

/**
 * Read and parse JSON file
 */
async function readJsonFile<T>(path: string, defaultValue: T): Promise<T> {
  try {
    const content = await fs.readFile(path, 'utf-8');
    return JSON.parse(content) as T;
  } catch (error: any) {
    if (error.code === 'ENOENT') {
      return defaultValue;
    }
    console.error(`Error reading ${path}:`, error);
    return defaultValue;
  }
}

/**
 * Convert timestamp from JSON format to Timestamp object
 */
function parseTimestamp(value: any): Timestamp | Date {
  if (!value) {
    return Timestamp.now();
  }
  
  if (value instanceof Date) {
    return value;
  }
  
  if (typeof value === 'string') {
    return Timestamp.fromDate(new Date(value));
  }
  
  if (typeof value === 'object' && 'seconds' in value) {
    return new Timestamp(value.seconds, value.nanoseconds || 0);
  }
  
  if (typeof value === 'number') {
    return Timestamp.fromMillis(value);
  }
  
  return Timestamp.now();
}

/**
 * Migrate studies from JSON to SQLite
 */
async function migrateStudies(): Promise<number> {
  const studies = await readJsonFile<Study[]>(STUDIES_JSON, []);
  
  if (studies.length === 0) {
    console.log('No studies found in JSON file');
    return 0;
  }
  
  console.log(`Migrating ${studies.length} studies...`);
  
  let migrated = 0;
  for (const study of studies) {
    try {
      // Convert timestamps
      const migratedStudy: Study = {
        ...study,
        createdAt: parseTimestamp(study.createdAt),
        updatedAt: parseTimestamp(study.updatedAt),
        completedAt: study.completedAt ? parseTimestamp(study.completedAt) : undefined,
        assets: study.assets || [],
        uploadedFiles: study.uploadedFiles || [],
        rooms: study.rooms || [],
        takeoffs: study.takeoffs || [],
      };
      
      createStudy(migratedStudy);
      migrated++;
    } catch (error) {
      console.error(`Failed to migrate study ${study.id}:`, error);
    }
  }
  
  console.log(`Successfully migrated ${migrated} studies`);
  return migrated;
}

/**
 * Migrate files from JSON to SQLite
 */
async function migrateFiles(): Promise<number> {
  const files = await readJsonFile<UploadedFile[]>(FILES_JSON, []);
  
  if (files.length === 0) {
    console.log('No files found in JSON file');
    return 0;
  }
  
  console.log(`Migrating ${files.length} files...`);
  
  let migrated = 0;
  for (const file of files) {
    try {
      createFile(file);
      migrated++;
    } catch (error) {
      console.error(`Failed to migrate file ${file.id}:`, error);
    }
  }
  
  console.log(`Successfully migrated ${migrated} files`);
  return migrated;
}

/**
 * Main migration function
 */
export async function migrate(): Promise<void> {
  console.log('Starting migration from JSON to SQLite...');
  
  // Check if database already exists with data
  if (await databaseExists()) {
    console.log('Database already exists with data. Skipping migration.');
    return;
  }
  
  // Check if JSON files exist
  const studiesExist = await fileExists(STUDIES_JSON);
  const filesExist = await fileExists(FILES_JSON);
  
  if (!studiesExist && !filesExist) {
    console.log('No JSON files found. Nothing to migrate.');
    return;
  }
  
  // Initialize database (creates schema)
  getDb();
  
  // Backup JSON files
  await backupJsonFiles();
  
  // Migrate data
  const studiesCount = await migrateStudies();
  const filesCount = await migrateFiles();
  
  console.log(`\nMigration complete!`);
  console.log(`- Migrated ${studiesCount} studies`);
  console.log(`- Migrated ${filesCount} files`);
  console.log(`- Database location: ${DB_PATH}`);
  console.log(`- JSON files backed up to: ${join(DATA_DIR, 'backup')}`);
}

// Run migration if called directly
if (require.main === module) {
  migrate().catch(error => {
    console.error('Migration failed:', error);
    process.exit(1);
  });
}

