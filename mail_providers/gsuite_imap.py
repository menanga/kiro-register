"""Self-hosted Google Workspace / IMAP catch-all mail provider.

Designed for users who own a pool of domains whose MX records forward every
alias (`*@domain.tld`) to a single Gmail/Workspace inbox. Creating a mailbox
simply invents a fresh `<random>@<random-domain>` alias — the inbound server
accepts anything, the catch-all routing forwards it here, and we poll the IMAP
account for the OTP message addressed to that alias.

Works out-of-the-box with:
- Google Workspace "Default routing" rule set to forward unknown addresses to a
  master inbox (Admin Console -> Apps -> Gmail -> Default routing).
- Cloudflare Email Routing catch-all forwarding to a Gmail account.
- Any other catch-all setup where a regular IMAP account receives everything.

Requires:
- An app password (Gmail) or a regular IMAP password.
- The pool of domains listed in a plain-text file (one domain per line) OR
  passed in as a list to the constructor.
"""
from __future__ import annotations

import email
import email.message
import email.utils
import imaplib
import os
import random
import re
import string
import time
from email.header import decode_header
from pathlib import Path

from .base import MailProvider


_DEFAULT_DOMAINS_FILE = Path(__file__).resolve().parent.parent / "domains.txt"


def _load_domains_from_file(path: str | Path) -> list[str]:
    p = Path(path)
    if not p.exists():
        return []
    return [line.strip().lower() for line in p.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")]


def _random_local(length: int = 10) -> str:
    pool = string.ascii_lowercase + string.digits
    return "".join(random.choices(pool, k=length))


def _decode_header_value(raw: str) -> str:
    """Best-effort decode of a MIME-encoded email header."""
    if not raw:
        return ""
    parts = []
    for chunk, enc in decode_header(raw):
        if isinstance(chunk, bytes):
            try:
                parts.append(chunk.decode(enc or "utf-8", errors="ignore"))
            except LookupError:
                parts.append(chunk.decode("utf-8", errors="ignore"))
        else:
            parts.append(chunk)
    return "".join(parts)


def _extract_bodies(msg: email.message.Message) -> list[str]:
    """Return every text/* part body as a list of decoded strings."""
    out: list[str] = []
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            if ctype.startswith("text/"):
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    try:
                        out.append(payload.decode(charset, errors="ignore"))
                    except LookupError:
                        out.append(payload.decode("utf-8", errors="ignore"))
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            try:
                out.append(payload.decode(charset, errors="ignore"))
            except LookupError:
                out.append(payload.decode("utf-8", errors="ignore"))
        elif msg.get_payload():
            out.append(str(msg.get_payload()))
    return out


class GsuiteImapProvider(MailProvider):
    """Catch-all IMAP provider (Google Workspace, Cloudflare Email Routing, etc.)."""

    name = "gsuite_imap"
    display_name = "Gsuite/IMAP (self-hosted)"

    def __init__(
        self,
        imap_server: str = "imap.gmail.com",
        imap_port: int = 993,
        imap_user: str = "",
        imap_pass: str = "",
        imap_folder: str = "INBOX",
        domains: list[str] | None = None,
        domains_file: str | Path | None = None,
        local_prefix: str = "",
        local_length: int = 10,
    ):
        self.imap_server = imap_server
        self.imap_port = int(imap_port) if imap_port else 993
        self.imap_user = imap_user
        self.imap_pass = imap_pass
        self.imap_folder = imap_folder or "INBOX"
        self.local_prefix = local_prefix or ""
        self.local_length = max(4, int(local_length))

        # Resolve the domain pool.
        pool: list[str] = []
        if domains:
            pool = [d.strip().lower() for d in domains if d and d.strip()]
        elif domains_file:
            pool = _load_domains_from_file(domains_file)
        else:
            pool = _load_domains_from_file(_DEFAULT_DOMAINS_FILE)
        if not pool:
            raise ValueError(
                "GsuiteImapProvider: empty domain pool. Pass domains=[...] or point "
                "domains_file to a non-empty file with one domain per line."
            )
        self.domains: list[str] = pool

        # Per-mailbox state populated by create_mailbox().
        self.address: str | None = None
        self._created_at: float = 0.0
        # Monotonically-bumped set of UIDs we've already consumed so the same
        # OTP isn't returned twice if the caller reuses the provider.
        self._seen_uids: set[str] = set()

    # ------------------------------------------------------------------
    # MailProvider interface
    # ------------------------------------------------------------------

    def create_mailbox(self) -> str:
        domain = random.choice(self.domains)
        local = f"{self.local_prefix}{_random_local(self.local_length)}"
        self.address = f"{local}@{domain}"
        self._created_at = time.time()
        self._seen_uids = set()
        return self.address

    def wait_otp(self, timeout: int = 120, poll_interval: int = 3) -> str:
        if not self.address:
            raise RuntimeError("Call create_mailbox() before wait_otp().")
        if not self.imap_user or not self.imap_pass:
            raise RuntimeError("IMAP credentials missing (imap_user / imap_pass).")

        deadline = time.time() + max(timeout, 1)
        target = self.address.lower()

        while time.time() < deadline:
            try:
                code = self._poll_once(target)
                if code:
                    return code
            except imaplib.IMAP4.error:
                # Transient IMAP error — reconnect on the next loop.
                pass
            except Exception:
                # Don't let exotic errors kill the polling loop.
                pass
            time.sleep(max(1, int(poll_interval)))
        return ""

    def list_domains(self) -> list[dict]:
        return [{"id": d, "domain": d} for d in self.domains]

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _connect(self) -> imaplib.IMAP4_SSL:
        imap = imaplib.IMAP4_SSL(self.imap_server, self.imap_port)
        imap.login(self.imap_user, self.imap_pass)
        imap.select(self.imap_folder, readonly=False)
        return imap

    def _poll_once(self, target_address: str) -> str:
        """Single IMAP poll iteration. Returns the 6-digit OTP or empty string."""
        imap = self._connect()
        try:
            # Narrow to messages delivered after create_mailbox().
            # IMAP SINCE granularity is day-level, so we always fetch at least
            # the current day and then filter in-memory by the actual timestamp.
            # Changed: Using 10 minutes window instead of 24 hours for faster, focused search
            since_date = time.strftime(
                "%d-%b-%Y", time.gmtime(max(self._created_at - 600, 0))
            )

            # Search for TO + SINCE (10 minutes window)
            print(f"[IMAP DEBUG] Searching for TO:{target_address} SINCE:{since_date}")
            status, data = imap.uid(
                "SEARCH", None, f'(SINCE "{since_date}" TO "{target_address}")'
            )
            uids: list[str] = []
            if status == "OK" and data and data[0]:
                uids = data[0].decode(errors="ignore").split()
                print(f"[IMAP DEBUG] Compound search found {len(uids)} messages")

            # COMMENTED: Fallback searches disabled for faster, focused search
            # # Fallback: some IMAP servers dislike the compound query above.
            # if not uids:
            #     print(f"[IMAP DEBUG] Trying fallback search (TO only)...")
            #     status, data = imap.uid("SEARCH", None, f'(TO "{target_address}")')
            #     if status == "OK" and data and data[0]:
            #         uids = data[0].decode(errors="ignore").split()
            #         print(f"[IMAP DEBUG] Fallback search found {len(uids)} messages")
            #
            # # If still no results, try searching ALL recent messages (last 24h)
            # if not uids:
            #     print(f"[IMAP DEBUG] Trying broad search (SINCE only)...")
            #     status, data = imap.uid("SEARCH", None, f'(SINCE "{since_date}")')
            #     if status == "OK" and data and data[0]:
            #         uids = data[0].decode(errors="ignore").split()
            #         print(f"[IMAP DEBUG] Broad search found {len(uids)} messages")

            # Scan newest first so we get the most recent OTP.
            for uid in reversed(uids):
                if uid in self._seen_uids:
                    continue
                self._seen_uids.add(uid)
                status, msg_data = imap.uid("FETCH", uid, "(BODY.PEEK[])")
                if status != "OK" or not msg_data or not msg_data[0]:
                    continue
                raw = msg_data[0][1] if isinstance(msg_data[0], tuple) else None
                if not raw:
                    continue
                msg = email.message_from_bytes(raw)

                # DEBUG: Log message headers
                from_addr = _decode_header_value(msg.get("From", ""))
                subject = _decode_header_value(msg.get("Subject", ""))
                print(f"[IMAP DEBUG] Message UID:{uid} From:{from_addr[:50]} Subject:{subject[:50]}")

                # Sanity: the `To:` (or `Delivered-To:` / `X-Original-To:`)
                # header should mention the target alias. Catch-all setups
                # keep the alias in the `To:` header, Cloudflare Routing
                # sometimes strips it into `X-Forwarded-To:` instead.
                to_haystack = " ".join(
                    _decode_header_value(msg.get(h, ""))
                    for h in ("To", "Delivered-To", "X-Original-To",
                              "X-Forwarded-To", "X-Delivered-To")
                ).lower()

                print(f"[IMAP DEBUG]   To headers: {to_haystack[:100]}")

                if target_address not in to_haystack:
                    print(f"[IMAP DEBUG]   ❌ SKIPPED: Target address not in To headers")
                    continue

                # Timestamp filter: ignore pre-mailbox-creation messages.
                ts = self._message_epoch(msg)
                if ts and ts + 5 < self._created_at:
                    print(f"[IMAP DEBUG]   ❌ SKIPPED: Message too old (ts={ts}, created={self._created_at})")
                    continue

                # Prefer the subject line (many OTP emails put the code in the
                # subject, e.g. "Your verification code is 123456").
                m = re.search(r"\b(\d{6})\b", subject)
                if m:
                    print(f"[IMAP DEBUG]   ✅ FOUND OTP in subject: {m.group(1)}")
                    return m.group(1)

                for body in _extract_bodies(msg):
                    m = re.search(r"\b(\d{6})\b", body)
                    if m:
                        print(f"[IMAP DEBUG]   ✅ FOUND OTP in body: {m.group(1)}")
                        return m.group(1)

                print(f"[IMAP DEBUG]   ⚠️  No 6-digit code found in this message")
        finally:
            try:
                imap.close()
            except Exception:
                pass
            try:
                imap.logout()
            except Exception:
                pass
        return ""

    @staticmethod
    def _message_epoch(msg: email.message.Message) -> float:
        date_hdr = msg.get("Date") or msg.get("Received", "")
        try:
            tup = email.utils.parsedate_tz(date_hdr)
            if tup:
                return email.utils.mktime_tz(tup)
        except Exception:
            pass
        return 0.0
