/**
 * Phase 22.1 — offline shift-start.
 *
 * CTO gate (audit of commit 120e734): "the current system supports:
 * Open register online -> internet lost -> continue selling. Not
 * supported: device starts offline -> cashier wants to open shift ->
 * checkout." This module closes that gap.
 *
 * Two things have to be true before a device can open a shift while
 * genuinely offline, and both are enforced here rather than trusted from
 * the renderer:
 *
 *   1. AUTHENTICATION must be provable without the server. A bcrypt hash
 *      of the cashier's password is cached HERE, in the main process,
 *      the moment an ONLINE login succeeds (cacheOfflineCredential(),
 *      called from electron/main.ts right after auth:save-token). This
 *      is not the server's own password hash and is never transmitted —
 *      it only ever proves "this is the same person who logged in online
 *      last time on this specific device."
 *
 *   2. REGISTER OWNERSHIP must be provable without the server. A cashier
 *      cannot be allowed to type in an arbitrary register_id while
 *      offline and have this module simply trust it — that would be
 *      exactly the "trust the caller's id" mistake the Phase 8.5 gate
 *      fixed for checkout and cash-session-open. Instead, the ONLY
 *      register a device may open offline is the one it was already
 *      proven, ONLINE, to be authorized for (saveKnownRegister(), called
 *      right after a real /api/v1/cash/session/open success).
 *
 * The actual "open shift" write mirrors checkoutOffline.ts's existing
 * pattern exactly: write the local session row and enqueue the outbox
 * event in the SAME better-sqlite3 transaction, so a crash between the
 * two can never happen. The server-side CashierSession is created only
 * once this event reaches app/api/v1/sync.py — see that file's new
 * "cash_session.open" handling, which independently re-validates
 * register/store/tenant ownership and the cash.manage_session permission
 * again, exactly as the online endpoint does. Nothing here is trusted
 * blindly by the server just because it arrived from a device.
 */
import Database from "better-sqlite3";
import bcrypt from "bcryptjs";
import { randomUUID } from "crypto";
import {
  getLocalAuthContext,
  getLocalRegisterSession,
  saveLocalRegisterSession,
  getOfflineCredential,
  saveOfflineCredential,
  getKnownRegisterId,
  LocalAuthContext,
} from "./sync/schema";
import { enqueueOutboxEvent } from "./sync/outboxSync";

export class OfflineAuthError extends Error {}
export class OfflineShiftError extends Error {}

const BCRYPT_ROUNDS = 10;

/** Called right after a successful ONLINE login (see main.ts). */
export function cacheOfflineCredential(db: Database.Database, email: string, password: string): void {
  const password_hash = bcrypt.hashSync(password, BCRYPT_ROUNDS);
  saveOfflineCredential(db, { email: email.toLowerCase(), password_hash });
}

/**
 * Verifies an offline login attempt against the cached credential and
 * returns the cached auth context on success. Throws OfflineAuthError
 * with a message safe to show the cashier on any failure — it never
 * reveals whether the email or the password was the wrong part, same
 * discipline a real login endpoint uses.
 */
export function verifyOfflineLogin(db: Database.Database, email: string, password: string): LocalAuthContext {
  const cred = getOfflineCredential(db);
  const ctx = getLocalAuthContext(db);
  if (!cred || !ctx) {
    throw new OfflineAuthError(
      "This device has no cached credentials — sign in online at least once before you can sign in offline"
    );
  }
  const emailMatches = cred.email === email.toLowerCase();
  const passwordMatches = bcrypt.compareSync(password, cred.password_hash);
  if (!emailMatches || !passwordMatches) {
    throw new OfflineAuthError("Incorrect email or password");
  }
  return ctx;
}

export interface OfflineShiftResult {
  registerId: number;
  openedAt: string;
  clientSessionId: string;
}

/**
 * Opens a cashier session using ONLY locally-verifiable facts. Returns
 * once the local session row + outbox event are durably written; the
 * outbox drains to the server (creating the real CashierSession row)
 * whenever connectivity returns, same as any other offline event.
 */
export function openShiftOffline(db: Database.Database, registerId: number, openingCashMinor: number): OfflineShiftResult {
  const ctx = getLocalAuthContext(db);
  if (!ctx) {
    throw new OfflineShiftError(
      "No cached login context on this device — sign in online at least once before opening a shift offline"
    );
  }
  if (ctx.store_id === null) {
    throw new OfflineShiftError("Cached user has no store assigned — cannot open a shift");
  }

  const knownRegisterId = getKnownRegisterId(db);
  if (knownRegisterId === undefined) {
    throw new OfflineShiftError(
      "This device has never opened a register online, so its register ownership cannot be verified offline — " +
        "open a register online at least once first"
    );
  }
  if (knownRegisterId !== registerId) {
    throw new OfflineShiftError(
      `This device is only known to be authorized for register ${knownRegisterId}, not ${registerId} — ` +
        "cannot open a different, unverified register while offline"
    );
  }

  if (getLocalRegisterSession(db)) {
    throw new OfflineShiftError("A cashier session is already open on this device");
  }

  const clientSessionId = randomUUID();
  const openedAt = new Date().toISOString();

  const tx = db.transaction(() => {
    // session_id = 0 is the "server hasn't confirmed this yet" sentinel.
    // checkoutOffline() only reads register_id/store_id/tenant_id off
    // this row (see its own code) so a sale can proceed immediately
    // regardless of whether the real server-side CashierSession id is
    // known yet.
    saveLocalRegisterSession(db, {
      session_id: 0,
      register_id: registerId,
      store_id: ctx.store_id as number,
      tenant_id: ctx.tenant_id,
      cashier_user_id: ctx.user_id,
      opened_at: openedAt,
      pending_sync: 1,
    });
    enqueueOutboxEvent(db, {
      aggregateType: "cash_session",
      aggregateId: clientSessionId,
      eventType: "cash_session.open",
      payload: {
        register_id: registerId,
        opening_cash_minor: openingCashMinor,
        client_session_id: clientSessionId,
      },
    });
  });
  tx();

  return { registerId, openedAt, clientSessionId };
}
