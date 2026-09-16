import { createHash, randomUUID } from 'node:crypto';
import { mkdtemp, readFile, rm, stat, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

export interface StoredTemporaryInput {
  readonly id: string;
  readonly path: string;
  readonly bytes: number;
  readonly createdAt: number;
  readonly expiresAt: number;
}

export interface TemporaryInputStore {
  save(id: string, data: Uint8Array | string): Promise<StoredTemporaryInput>;
  read(id: string): Promise<Buffer>;
  exists(id: string): Promise<boolean>;
  cleanup(id: string): Promise<void>;
  cleanupExpired(): Promise<readonly string[]>;
  list(): readonly StoredTemporaryInput[];
  cleanupAll(): Promise<void>;
}

export interface TemporaryInputStoreOptions {
  /** Parent directory; defaults to the OS temp dir. */
  readonly root?: string;
  /**
   * Emergency TTL. The final retention period is still an open decision, so
   * the default stays deliberately short.
   */
  readonly ttlMs?: number;
  readonly now?: () => number;
}

const DEFAULT_TTL_MS = 60 * 60 * 1000;

function fileNameFor(id: string): string {
  return `${createHash('sha256').update(id).digest('hex')}.bin`;
}

/**
 * Keeps extracted attachment bytes on disk with owner-only permissions, so a
 * crash cannot leave readable resumes behind. Every entry is removed on
 * cleanup, on cancellation, or by TTL.
 */
export async function createTemporaryInputStore(
  options: TemporaryInputStoreOptions = {},
): Promise<TemporaryInputStore> {
  const now = options.now ?? (() => Date.now());
  const ttlMs = options.ttlMs ?? DEFAULT_TTL_MS;
  const root = await mkdtemp(join(options.root ?? tmpdir(), `hr-agent-${randomUUID().slice(0, 8)}-`));
  const entries = new Map<string, StoredTemporaryInput>();

  await writeFile(join(root, '.keep'), '', { mode: 0o600 }).catch(() => undefined);

  const save: TemporaryInputStore['save'] = async (id, data) => {
    if (id.trim().length === 0) {
      throw new Error('id временного вложения не может быть пустым.');
    }
    const buffer = typeof data === 'string' ? Buffer.from(data, 'utf8') : Buffer.from(data);
    const path = join(root, fileNameFor(id));
    await writeFile(path, buffer, { mode: 0o600 });
    const createdAt = now();
    const entry: StoredTemporaryInput = {
      id,
      path,
      bytes: buffer.byteLength,
      createdAt,
      expiresAt: createdAt + ttlMs,
    };
    entries.set(id, entry);
    return entry;
  };

  const read: TemporaryInputStore['read'] = async (id) => {
    const entry = entries.get(id);
    if (entry === undefined) {
      throw new Error(`Временное вложение «${id}» не найдено.`);
    }
    return readFile(entry.path);
  };

  const exists: TemporaryInputStore['exists'] = async (id) => {
    const entry = entries.get(id);
    if (entry === undefined) {
      return false;
    }
    try {
      await stat(entry.path);
      return true;
    } catch {
      return false;
    }
  };

  const cleanup: TemporaryInputStore['cleanup'] = async (id) => {
    const entry = entries.get(id);
    if (entry === undefined) {
      return;
    }
    await rm(entry.path, { force: true });
    entries.delete(id);
  };

  const cleanupExpired: TemporaryInputStore['cleanupExpired'] = async () => {
    const current = now();
    const removed: string[] = [];
    for (const entry of [...entries.values()]) {
      if (entry.expiresAt <= current) {
        await rm(entry.path, { force: true });
        entries.delete(entry.id);
        removed.push(entry.id);
      }
    }
    return removed;
  };

  const cleanupAll: TemporaryInputStore['cleanupAll'] = async () => {
    entries.clear();
    await rm(root, { recursive: true, force: true });
  };

  const list: TemporaryInputStore['list'] = () => [...entries.values()];

  return { save, read, exists, cleanup, cleanupExpired, list, cleanupAll };
}
