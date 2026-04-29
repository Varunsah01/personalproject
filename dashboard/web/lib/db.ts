import Database from "better-sqlite3";
import path from "path";

const DB_PATH = path.resolve(process.cwd(), "../../outreach/data/pipeline.db");

let db: Database.Database | null = null;

export function getDb(): Database.Database {
  if (!db) {
    db = new Database(DB_PATH, { readonly: false });
    db.pragma("journal_mode = WAL");
    db.pragma("busy_timeout = 5000");
  }
  return db;
}

export const PROJECT_ROOT = path.resolve(process.cwd(), "../../");
