import { Pool } from "pg";

/**
 * Single Postgres pool for the server. `DATABASE_URL` is the whole contract:
 * point it at local Postgres for dev or a Neon pooled connection string in
 * production — no code change (Neon URLs carry `sslmode=require`, which pg
 * honours).
 */
declare global {
  // eslint-disable-next-line no-var
  var __seneddPool: Pool | undefined;
}

export function getPool(): Pool {
  if (!global.__seneddPool) {
    const connectionString = process.env.DATABASE_URL;
    if (!connectionString) {
      throw new Error("DATABASE_URL is not set (see apps/web/.env.example)");
    }
    global.__seneddPool = new Pool({ connectionString, max: 5 });
  }
  return global.__seneddPool;
}
