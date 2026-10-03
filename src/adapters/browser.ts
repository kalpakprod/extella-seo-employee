import { lookup } from 'node:dns/promises';
import type { TextBlock } from '../contracts/analysis.js';

export interface BrowserTab {
  readonly id: string;
  readonly url: string;
  readonly title?: string;
}

export interface BrowserTarget {
  readonly url: string;
  /** When set, only this already-open tab is read. */
  readonly tabId?: string;
  /** True when HR opened the page themselves, which is the preferred flow. */
  readonly userOpened?: boolean;
}

export type BrowserReadResult =
  | {
      readonly status: 'readable';
      readonly url: string;
      readonly title?: string;
      readonly blocks: readonly TextBlock[];
      readonly warnings: readonly string[];
    }
  | { readonly status: 'needs_login'; readonly url: string; readonly reason: string }
  | { readonly status: 'unavailable'; readonly url: string; readonly reason: string }
  | { readonly status: 'blocked'; readonly url: string; readonly reason: string };

export interface BrowserPort {
  listOpenTabs(): Promise<readonly BrowserTab[]>;
  readSubmittedPage(target: BrowserTarget, signal?: AbortSignal): Promise<BrowserReadResult>;
}

const FORBIDDEN_HOST_SUFFIXES = ['.internal', '.local', '.localhost', '.home', '.lan'];

function isPrivateIpv4(address: string): boolean {
  const octets = address.split('.').map((part) => Number.parseInt(part, 10));
  if (octets.length !== 4 || octets.some((octet) => Number.isNaN(octet))) {
    return false;
  }
  const [a, b] = octets as [number, number, number, number];
  if (a === 0 || a === 10 || a === 127) return true;
  if (a === 169 && b === 254) return true;
  if (a === 172 && b >= 16 && b <= 31) return true;
  if (a === 192 && b === 168) return true;
  if (a === 100 && b >= 64 && b <= 127) return true;
  if (a >= 224) return true;
  return false;
}

function isPrivateIpv6(address: string): boolean {
  const normalized = address.toLowerCase().replace(/^\[|\]$/g, '');
  if (normalized === '::1' || normalized === '::') return true;
  const firstHextet = Number.parseInt(normalized.split(':')[0] ?? '', 16);
  if (Number.isFinite(firstHextet)) {
    if ((firstHextet & 0xffc0) === 0xfe80) return true;
    if ((firstHextet & 0xfe00) === 0xfc00) return true;
    if ((firstHextet & 0xff00) === 0xff00) return true;
  }
  // Fail closed for IPv4-mapped IPv6. DNS lookup normally returns the native
  // IPv4 form; accepting alternate mapped forms risks bypassing the IPv4
  // private-range checks (for example ::ffff:7f00:1).
  if (/(^|:)ffff:/u.test(normalized)) return true;
  return false;
}

/** Host-level deny list applied before any DNS resolution. */
export function isForbiddenHost(hostname: string): boolean {
  const host = hostname.trim().toLowerCase().replace(/\.$/, '');
  if (host.length === 0) return true;
  if (host === 'localhost' || host.endsWith('.localhost')) return true;
  if (host === 'metadata.google.internal' || host === 'metadata' || host === '169.254.169.254') return true;
  if (FORBIDDEN_HOST_SUFFIXES.some((suffix) => host.endsWith(suffix))) return true;
  if (host.startsWith('[') || host.includes(':')) return isPrivateIpv6(host);
  if (/^\d{1,3}(\.\d{1,3}){3}$/.test(host)) return isPrivateIpv4(host);
  return false;
}

export interface UrlPolicyOptions {
  /** Injectable resolver so the policy can be unit-tested without network access. */
  readonly resolve?: (hostname: string) => Promise<readonly string[]>;
}

export class UrlBlockedError extends Error {
  constructor(
    readonly url: string,
    reason: string,
  ) {
    super(`Ссылка «${url}» заблокирована: ${reason}`);
    this.name = 'UrlBlockedError';
  }
}

/** Parses a submitted URL and applies checks that do not require DNS. */
export function assertUrlShapeAllowed(raw: string): URL {
  let url: URL;
  try {
    url = new URL(raw.trim());
  } catch {
    throw new UrlBlockedError(raw, 'некорректный URL');
  }
  if (url.protocol !== 'http:' && url.protocol !== 'https:') {
    throw new UrlBlockedError(raw, 'разрешены только http/https');
  }
  if (url.username.length > 0 || url.password.length > 0) {
    throw new UrlBlockedError(raw, 'URL с логином и паролем не принимается');
  }
  if (isForbiddenHost(url.hostname)) {
    throw new UrlBlockedError(raw, 'локальные, приватные и metadata-адреса запрещены');
  }
  return url;
}

/**
 * Allows only http/https URLs that do not resolve to local, private,
 * link-local or metadata addresses. All addresses of a host are checked.
 */
export async function assertUrlAllowed(raw: string, options: UrlPolicyOptions = {}): Promise<URL> {
  const url = assertUrlShapeAllowed(raw);
  const resolve =
    options.resolve ??
    (async (hostname: string) => {
      const records = await lookup(hostname, { all: true });
      return records.map((record) => record.address);
    });
  let addresses: readonly string[];
  try {
    addresses = await resolve(url.hostname);
  } catch {
    throw new UrlBlockedError(raw, 'DNS-имя не разрешилось');
  }
  if (addresses.length === 0) {
    throw new UrlBlockedError(raw, 'DNS не вернул ни одного адреса');
  }
  for (const address of addresses) {
    if (isPrivateIpv4(address) || isPrivateIpv6(address)) {
      throw new UrlBlockedError(raw, `адрес ${address} приватный`);
    }
  }
  return url;
}

/**
 * A redirect may not silently leave the host HR pointed the agent at; crossing
 * to another host requires an explicit HR action.
 */
export function assertRedirectAllowed(previous: string, next: string): URL {
  const from = assertUrlShapeAllowed(previous);
  const to = assertUrlShapeAllowed(next);
  if (to.hostname !== from.hostname) {
    throw new UrlBlockedError(next, 'переход на другой домен требует подтверждения HR');
  }
  if (from.protocol === 'https:' && to.protocol !== 'https:') {
    throw new UrlBlockedError(next, 'переход с HTTPS на HTTP запрещён');
  }
  return to;
}

export function createUnavailableBrowserPort(reason: string): BrowserPort {
  return {
    listOpenTabs: () => Promise.resolve([]),
    readSubmittedPage: (target) =>
      Promise.resolve({ status: 'unavailable', url: target.url, reason } satisfies BrowserReadResult),
  };
}

/** Asks HR to open the page and hand it over; never accepts credentials. */
export function createManualBrowserPort(reason: string): BrowserPort {
  return {
    listOpenTabs: () => Promise.resolve([]),
    readSubmittedPage: (target) =>
      Promise.resolve({ status: 'needs_login', url: target.url, reason } satisfies BrowserReadResult),
  };
}

export interface InMemoryPage {
  readonly url: string;
  readonly status: BrowserReadResult['status'];
  readonly title?: string;
  readonly text?: string;
  readonly reason?: string;
}

/**
 * Deterministic stand-in used by tests and by the integration spike until the
 * Extella browser profile contract is confirmed.
 */
export function createInMemoryBrowserPort(pages: readonly InMemoryPage[]): BrowserPort {
  const byUrl = new Map(pages.map((page) => [page.url, page]));
  return {
    listOpenTabs: () =>
      Promise.resolve(pages.map((page, index) => ({ id: `tab-${index + 1}`, url: page.url, ...(page.title === undefined ? {} : { title: page.title }) }))),
    readSubmittedPage: (target) => {
      const page = byUrl.get(target.url);
      if (page === undefined) {
        return Promise.resolve({ status: 'unavailable', url: target.url, reason: 'Страница не открыта в профиле агента.' } satisfies BrowserReadResult);
      }
      switch (page.status) {
        case 'readable':
          return Promise.resolve({
            status: 'readable',
            url: page.url,
            ...(page.title === undefined ? {} : { title: page.title }),
            blocks: page.text === undefined ? [] : [{ id: `${target.tabId ?? 'page'}-b1`, text: page.text }],
            warnings: [],
          } satisfies BrowserReadResult);
        case 'needs_login':
          return Promise.resolve({ status: 'needs_login', url: page.url, reason: page.reason ?? 'Требуется вход HR.' } satisfies BrowserReadResult);
        case 'blocked':
          return Promise.resolve({ status: 'blocked', url: page.url, reason: page.reason ?? 'Адрес запрещён политикой.' } satisfies BrowserReadResult);
        default:
          return Promise.resolve({ status: 'unavailable', url: page.url, reason: page.reason ?? 'Страница недоступна.' } satisfies BrowserReadResult);
      }
    },
  };
}
