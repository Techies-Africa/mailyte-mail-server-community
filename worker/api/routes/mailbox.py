"""
The mailbox-holder surface -- /api/v1/mailbox/*

Part of 04-mailyte-web/02-PRD-webmail-standalone. Replaces mailyte-api's
/api/v1/mailbox/*, keeping the same paths and shapes so the webmail changes
only its base URL. Phases 2-5 add mail, send, settings, security and Sieve
handlers to this router; Phase 1 establishes the router and the capability
manifest they register into.

**No handler in this file takes an account id from the path or the body.**
The session says which mailbox this is. That is what makes cross-mailbox
access structurally impossible rather than a check someone has to remember,
and it is why none of these routes carry an {account} segment.
"""

import io
import logging
import os
import re
from datetime import datetime
from email.utils import getaddresses
from urllib.parse import quote

import qrcode
import qrcode.image.svg
import requests
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

# starlette's UploadFile, NOT fastapi's. fastapi.UploadFile is a SUBCLASS,
# and form parsing yields the starlette base class -- so isinstance against
# the fastapi one is always False and every attachment is silently dropped
# while the send still reports success. Verified: fastapi.UploadFile is
# starlette.datastructures.UploadFile -> False.
from starlette.datastructures import UploadFile
from utils.auth import create_api_response
from utils.database import get_db_connection
from utils.mailbox_auth import require_mailbox
from utils.managesieve import ManageSieveClient, ManageSieveError
from utils.sieve_compilers import (
    FORWARDING_SCRIPT,
    MAIN_SCRIPT,
    MAX_FORWARD_ADDRESSES,
    MAX_RULES,
    RULES_SCRIPT,
    VACATION_SCRIPT,
    clean_addresses,
    compile_forwarding,
    compile_main,
    compile_rules,
    compile_vacation,
    parse_forwarding,
    parse_rules,
    parse_vacation,
)
from utils.signature_html import sanitize_signature

from shared.imap_mail import (
    MAX_ATTACHMENT_BYTES,
    MAX_ATTACHMENTS,
    MAX_TOTAL_ATTACHMENT_BYTES,
    FolderNotEmptyError,
    FolderProtectedError,
    ImapUnavailableError,
    append_message,
    build_message,
    compose_message_id,
    create_folder,
    delete_folder,
    expunge_message,
    extract_attachment,
    fetch_raw_message,
    fetch_summaries,
    folder_id,
    harvest_contacts,
    imap_session,
    list_folders,
    mark_answered,
    message_bytes,
    move_message,
    parse_message,
    quota,
    raw_download_name,
    rename_folder,
    search_uids,
    set_flag,
    split_message_id,
    submit_message,
    thread_for,
)
from shared.ulid_utils import generate_ulid

logger = logging.getLogger(__name__)

router = APIRouter()


def _unavailable(exc: ImapUnavailableError) -> HTTPException:
    """Dovecot problems are 502s, never 4xx.

    Nothing the mailbox holder sends can fix an unreachable mail server, and
    reporting it as a client error sends them to re-check a password that was
    never involved -- the master credential is what failed.
    """
    logger.error("Mailbox IMAP unavailable: %s", exc)
    return HTTPException(
        status_code=502,
        detail=create_api_response("error", str(exc) or "Mail server unavailable"),
    )


def _ai_configured() -> bool:
    """AI is optional and BYO-endpoint.

    Mailyte points AI_BASE_URL at Azure OpenAI; a self-hoster points it at
    their own key, a local model, or nothing at all. Nothing about the
    webmail's AI features is Mailyte-specific except the credential, so the
    credential is the only thing that decides whether AI EXISTS here.

    The single definition lives in utils/ai_consent (it is also gate 1 of
    resolve_maya); delegating keeps /capabilities and the gate from ever
    disagreeing. Local import: the top of this file is shared territory.
    """
    from utils.ai_consent import ai_configured

    return ai_configured()


def _sieve_credentials_configured() -> bool:
    """The deployment has a master credential to speak ManageSieve with at all."""
    return bool(
        os.getenv("IMAP_MASTER_USER", "").strip() and os.getenv("IMAP_MASTER_PASSWORD", "").strip()
    )


def _sieve_configured() -> bool:
    """Rules, forwarding, the vacation responder and blocked senders all
    compile to Sieve and reach Dovecot over ManageSieve using a master user.
    Without that credential the whole group is unavailable, not merely
    degraded -- and the credential alone is not the answer. This is what
    /capabilities advertises, and the webmail renders nothing for a
    capability that is not advertised but a broken control for one that is;
    so the question is "can it be served right now", answered by a cached
    connectivity probe (utils.managesieve.sieve_available, SIEVE_PROBE_TTL
    seconds) that real requests keep honest through report_failure /
    report_success."""
    from utils import managesieve

    if not _sieve_credentials_configured():
        return False
    return managesieve.sieve_available()


@router.get(
    "/capabilities",
    summary="What this deployment's webmail can actually do",
    description=(
        "Runtime feature detection for the webmail: which optional services are "
        "configured on THIS deployment. Distinct from /api/v1/capabilities, which "
        "reports the build's edition (CE vs Pro) and cannot answer questions like "
        "'is an AI endpoint configured', since two deployments of the same edition "
        "differ. The webmail hides any control this does not advertise."
    ),
)
def capabilities(mailbox: dict = Depends(require_mailbox)):
    """Authenticated: this describes how a deployment is configured, which is
    not something to hand to an anonymous caller. The webmail fetches it
    immediately after sign-in, before rendering anything optional.

    This is a public contract consumed by the open-source webmail. Renaming a
    key here is a breaking change for every self-hoster -- add, don't rename.
    """
    from utils.ai_consent import entitlements_for_mailbox

    return create_api_response(
        "success",
        "Capabilities",
        {
            "email_address": mailbox["email"],
            "capabilities": {
                # Phase 2-3: always present, they are the mail client itself.
                "mail": True,
                "send": True,
                # Phase 4.
                "settings": True,
                "two_factor": True,
                # Phase 5 -- genuinely absent without a master credential.
                "rules": _sieve_configured(),
                "forwarding": _sieve_configured(),
                "vacation": _sieve_configured(),
                # Phase 4, optional and BYO.
                "ai": _ai_configured(),
            },
            # Mobile v1 13a. ALWAYS emitted: the client treats an absent
            # entitlements block as "nothing entitled", so omitting it on a
            # database error would be indistinguishable from a paying org
            # losing Maya -- entitlements_for_mailbox fails closed to the
            # same shape instead. `maya` is the org's plan entitlement only;
            # whether Maya actually runs is also gated by capabilities.ai,
            # maya_org_policy and individual consent (utils/ai_consent).
            "entitlements": entitlements_for_mailbox(mailbox),
            # Shared mailboxes this person is a member of. The webmail needs
            # this to offer a From selector: the shared FOLDERS it can already
            # see say what may be read, which is not the same question --
            # read_only can open the mailbox and must not be offered as a
            # sending identity. Fails closed to [] (see below).
            "shared_mailboxes": shared_mailboxes_for(mailbox["email"]),
        },
    )


SENDING_PERMISSIONS = ("full_access", "send_as", "send_on_behalf")


def shared_mailboxes_for(member_email: str) -> list[dict]:
    """Shared mailboxes this member belongs to, with what they may do.

    `can_send` is pre-computed rather than left to the client to derive from
    `permission`: the authoritative copy of that rule lives here and in
    Postfix's sender-login map, and a third copy in TypeScript would be a
    third place for it to drift.

    Returns [] on any database error. The webmail then offers no shared
    identity, which is the safe direction -- the send itself is checked
    again server-side regardless (see _shared_mailbox_permission), so this
    list is a convenience, never the security boundary.
    """
    connection = get_db_connection()
    if not connection:
        return []

    try:
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT shared.email AS address, shared.name AS name, smm.permission
            FROM shared_mailbox_members smm
            JOIN email_accounts shared
              ON shared.id = smm.shared_mailbox_id
             AND shared.mailbox_type = 'shared'
             AND shared.status = 'active'
            JOIN email_accounts member
              ON member.id = smm.email_account_id
             AND member.status = 'active'
            WHERE member.email = %s
            ORDER BY shared.email
            """,
            (member_email,),
        )
        rows = cursor.fetchall()
        cursor.close()
        return [
            {
                "address": row["address"],
                "name": row["name"] or "",
                "permission": row["permission"],
                "can_send": row["permission"] in SENDING_PERMISSIONS,
            }
            for row in rows
        ]
    except Exception as exc:  # noqa: BLE001 -- a capabilities call must not 500
        logger.error("Shared-mailbox list failed for %s: %s", member_email, exc)
        return []
    finally:
        connection.close()


# ---------------------------------------------------------------------------
# Folders (PRD Phase 2)
# ---------------------------------------------------------------------------


class CreateFolderRequest(BaseModel):
    # IMAP folder names cannot contain the hierarchy delimiter or control
    # characters. Rejecting them here produces a real message instead of an
    # opaque mail-server failure. Same expression the Laravel route used, so
    # a name accepted before is still accepted.
    name: str = Field(..., min_length=1, max_length=255, pattern=r"^[^/\\\x00-\x1F]+$")


@router.get(
    "/folders",
    summary="List the mailbox's folders",
    description=(
        "Every folder with its message and unread counts plus IMAP's UIDNEXT and "
        "UIDVALIDITY. Polling those two is the webmail's delta check: unchanged "
        "tokens mean nothing arrived, moved or was deleted, for one request that "
        "transfers no messages."
    ),
)
def folders(mailbox: dict = Depends(require_mailbox)):
    try:
        with imap_session(mailbox["email"]) as conn:
            return create_api_response(
                "success", "Folders retrieved successfully", list_folders(conn)
            )
    except ImapUnavailableError as exc:
        raise _unavailable(exc) from None


@router.post(
    "/folders",
    summary="Create a folder",
    description=(
        "Creates and subscribes an IMAP folder. This is also what the webmail's "
        "label management calls -- a label IS a folder here, not a separate "
        "concept invented in the UI."
    ),
)
def create_folder_route(body: CreateFolderRequest, mailbox: dict = Depends(require_mailbox)):
    try:
        with imap_session(mailbox["email"]) as conn:
            create_folder(conn, body.name)
            return create_api_response("success", "Folder created successfully", list_folders(conn))
    except ValueError as exc:
        # The server refused the name (already exists, bad hierarchy). That is
        # the caller's problem to fix, so 400 rather than 502.
        raise HTTPException(
            status_code=400, detail=create_api_response("error", str(exc))
        ) from None
    except ImapUnavailableError as exc:
        raise _unavailable(exc) from None


class RenameFolderRequest(BaseModel):
    # The new LAST segment only; same character rules as creation.
    name: str = Field(..., min_length=1, max_length=255, pattern=r"^[^/\\\x00-\x1F]+$")


def _folder_entry(conn, ident: str) -> dict:
    """The folder a path identifier names, or 404.

    Identifiers are the `id` values GET /folders returns -- folder_id(), the
    md5 of the name -- because a folder NAME can contain "/", Dovecot's
    hierarchy separator here, which no path segment can carry. The literal
    name is accepted too so `DELETE /folders/Receipts` reads naturally for a
    flat folder; the id is the form a client should store.
    """
    folders = list_folders(conn)
    for folder in folders:
        if folder["id"] == ident:
            return folder
    for folder in folders:
        if folder["name"] == ident:
            return folder
    raise HTTPException(status_code=404, detail=create_api_response("error", "Folder not found"))


@router.patch(
    "/folders/{folder}",
    summary="Rename a folder",
    description=(
        "`folder` is the id GET /folders returns. `name` is the new last segment; a "
        "nested folder keeps its parent, and RENAME carries its subfolders along. "
        "INBOX and the special-use folders (Sent, Drafts, Junk, Trash, Archive -- "
        "detected by SPECIAL-USE attribute and by well-known name) answer 409 "
        "`cannot_rename_special_folder`."
    ),
)
def rename_folder_route(
    folder: str, body: RenameFolderRequest, mailbox: dict = Depends(require_mailbox)
):
    try:
        with imap_session(mailbox["email"]) as conn:
            entry = _folder_entry(conn, folder)
            try:
                new_name = rename_folder(conn, entry["name"], body.name)
            except FolderProtectedError as exc:
                raise HTTPException(
                    status_code=409,
                    detail=create_api_response(
                        "error", str(exc), error_code="cannot_rename_special_folder"
                    ),
                ) from None
            return create_api_response(
                "success",
                "Folder renamed",
                {"id": folder_id(new_name), "name": new_name, "folders": list_folders(conn)},
            )
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail=create_api_response("error", str(exc))
        ) from None
    except ImapUnavailableError as exc:
        raise _unavailable(exc) from None


@router.delete(
    "/folders/{folder}",
    summary="Delete an empty folder",
    description=(
        "`folder` is the id GET /folders returns. Refused with 409 "
        "`cannot_delete_special_folder` for INBOX and the special-use folders, and "
        "with 409 `folder_not_empty` (messages) or `folder_has_subfolders` while "
        "anything is still inside -- IMAP DELETE destroys contents without a Trash "
        "step, so the holder empties the folder deliberately first. The client "
        "should warn on 409 rather than retry."
    ),
)
def delete_folder_route(folder: str, mailbox: dict = Depends(require_mailbox)):
    try:
        with imap_session(mailbox["email"]) as conn:
            entry = _folder_entry(conn, folder)
            try:
                delete_folder(conn, entry["name"])
            except FolderProtectedError as exc:
                raise HTTPException(
                    status_code=409,
                    detail=create_api_response(
                        "error", str(exc), error_code="cannot_delete_special_folder"
                    ),
                ) from None
            except FolderNotEmptyError as exc:
                raise HTTPException(
                    status_code=409,
                    detail=create_api_response(
                        "error",
                        str(exc),
                        error_code=(
                            "folder_not_empty"
                            if exc.reason == "messages"
                            else "folder_has_subfolders"
                        ),
                    ),
                ) from None
            return create_api_response("success", "Folder deleted", list_folders(conn))
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail=create_api_response("error", str(exc))
        ) from None
    except ImapUnavailableError as exc:
        raise _unavailable(exc) from None


# ---------------------------------------------------------------------------
# Messages (PRD Phase 2)
# ---------------------------------------------------------------------------

# A page the webmail actually renders. Large enough that scrolling rarely
# waits, small enough that one FETCH stays one round trip.
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200


@router.get(
    "/messages",
    summary="List messages in a folder",
    description=(
        "Newest first, server-side searched and paged. `q` is resolved by IMAP "
        "SEARCH against Dovecot's own indexes -- including message bodies -- so a "
        "word buried deep in an email is findable, which a client-side search over "
        "preview text can never be."
    ),
)
def messages(
    folder: str | None = Query(None, max_length=255),
    # The webmail sends `search`; `q` is the shorter name a client written
    # against this API would reach for. Both accepted -- honouring only `q`
    # makes the webmail's search box silently return the whole folder
    # unfiltered, which looks like "search is broken" and reports no error.
    search: str | None = Query(None, max_length=255),
    q: str | None = Query(None, max_length=255),
    unread: bool = Query(False),
    starred: bool = Query(False),
    limit: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    offset: int = Query(0, ge=0),
    mailbox: dict = Depends(require_mailbox),
):
    query = search or q
    try:
        with imap_session(mailbox["email"]) as conn:
            # Omitting `folder` while passing a search is how the client asks
            # for a whole-account search; a folderless browse still means
            # INBOX. Searching every folder for an ordinary browse would be
            # both meaningless and expensive.
            if folder:
                targets = [folder]
            elif query:
                targets = [f["name"] for f in list_folders(conn)]
            else:
                targets = ["INBOX"]

            hits: list[tuple[str, str]] = []
            for name in targets:
                for uid in search_uids(
                    conn, name, query=query, unread_only=unread, starred_only=starred
                ):
                    hits.append((name, uid))

            # Page over the id list, not over the fetch: SEARCH returns matches
            # as ids only, which is cheap, and only the visible slice is
            # fetched. Single-folder results are already newest-first from
            # SORT; a cross-folder search is re-sorted below once the dates
            # are known, since per-folder ordering says nothing across folders.
            page = hits[offset : offset + limit]
            summaries: list[dict] = []
            for name in dict.fromkeys(name for name, _ in page):
                summaries += fetch_summaries(
                    conn, name, [uid for folder_name, uid in page if folder_name == name]
                )
            if len(targets) > 1:
                summaries.sort(key=lambda m: m["received_at"] or "", reverse=True)

            return create_api_response(
                "success",
                "Messages retrieved successfully",
                {
                    "messages": summaries,
                    "total": len(hits),
                    "offset": offset,
                    "limit": limit,
                    # The client's MessagePage declares has_more and pages on
                    # it; without the field it is undefined, which is falsy,
                    # and the list stops after the first page.
                    "has_more": offset + limit < len(hits),
                    "folder": folder or ("" if query else "INBOX"),
                },
            )
    except ImapUnavailableError as exc:
        raise _unavailable(exc) from None


def _resolve(conn, message_id: str, fallback_folder: str = "INBOX") -> tuple[str, str]:
    """(folder, uid) from a scoped message id.

    Ids are folder-scoped because an IMAP UID is only unique within one
    mailbox -- a bare UID is ambiguous the moment a client holds messages from
    two folders, which the webmail always does.
    """
    folder, uid = split_message_id(message_id)
    if not uid.isdigit():
        raise HTTPException(
            status_code=400,
            detail=create_api_response("error", "Malformed message id"),
        )
    return folder or fallback_folder, uid


def _trash_folder(conn) -> str:
    """The folder Dovecot actually calls Trash on this deployment.

    Resolved from the folder roles rather than hardcoded: a mailbox migrated
    from another system can have "Deleted Items" or a localised name, and
    trashing into a folder that does not exist would fail or, worse, silently
    create one.
    """
    for folder in list_folders(conn):
        if folder["role"] == "trash":
            return folder["name"]
    return "Trash"


# ---- Drafts -------------------------------------------------------------
# Declared BEFORE /messages/{message_id} so "draft" is never captured as an
# id -- the same ordering hazard the Laravel routes documented for "send".


class SaveDraftRequest(BaseModel):
    # Recipients arrive as arrays -- the webmail's DraftPayload declares
    # `to: string[]`. A plain string is accepted too, so a client that sends
    # one comma-separated field is not rejected; _addresses() splits either.
    # Typing these as `str` only would 422 every autosave the webmail makes.
    to: list[str] | str | None = None
    cc: list[str] | str | None = None
    bcc: list[str] | str | None = None
    subject: str | None = Field(None, max_length=1000)
    body_html: str | None = Field(None, max_length=5_000_000)
    body_text: str | None = Field(None, max_length=5_000_000)
    in_reply_to: str | None = Field(None, max_length=998)
    references: str | None = Field(None, max_length=4000)
    # When replacing an existing draft, the old one is expunged after the new
    # one lands -- never before, so a failure cannot lose the only copy.
    #
    # The webmail calls this `replace_id`; `replaces_id` reads better and was
    # this API's first name for it. Both accepted: honouring only one leaves
    # the superseded draft in place, so every autosave adds another copy to
    # the Drafts folder and nothing reports a problem.
    replace_id: str | None = Field(None, max_length=512)
    replaces_id: str | None = Field(None, max_length=512)

    def recipients(self, which: str) -> list[str]:
        value = getattr(self, which)
        return [value] if isinstance(value, str) else list(value or [])

    @property
    def superseded_id(self) -> str | None:
        return self.replace_id or self.replaces_id


@router.post(
    "/messages/draft",
    summary="Save a draft",
    description=(
        "Appends the draft to the Drafts folder as a real message, so it is "
        "readable by every other client on the account rather than living in a "
        "webmail-only table."
    ),
)
def save_draft(body: SaveDraftRequest, mailbox: dict = Depends(require_mailbox)):
    try:
        with imap_session(mailbox["email"]) as conn:
            drafts = next(
                (f["name"] for f in list_folders(conn) if f["role"] == "drafts"), "Drafts"
            )
            # The same builder that assembles a sent message, so a draft
            # cannot turn into a subtly different email when it is sent.
            raw = message_bytes(
                build_message(
                    from_address=mailbox["email"],
                    to=_addresses(body.recipients("to")),
                    cc=_addresses(body.recipients("cc")),
                    bcc=_addresses(body.recipients("bcc")),
                    subject=body.subject,
                    body_text=body.body_text,
                    body_html=body.body_html,
                    in_reply_to=body.in_reply_to,
                    references=body.references,
                )
            )
            uid = append_message(conn, drafts, raw)

            # Replace only after the new copy exists.
            if body.superseded_id:
                old_folder, old_uid = _resolve(conn, body.superseded_id, drafts)
                try:
                    expunge_message(conn, old_folder, old_uid)
                except (ValueError, ImapUnavailableError) as exc:
                    # A stale replaces_id leaves a duplicate draft, which is
                    # recoverable; failing the save is not.
                    logger.warning("Could not expunge replaced draft: %s", exc)

            return create_api_response(
                "success",
                "Draft saved",
                {"id": compose_message_id(drafts, uid) if uid else None, "folder": drafts},
            )
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail=create_api_response("error", str(exc))
        ) from None
    except ImapUnavailableError as exc:
        raise _unavailable(exc) from None


@router.delete(
    "/messages/draft/{message_id}",
    summary="Discard a draft",
    description="Permanently removes a draft. Allowed outside Trash because a "
    "draft the author just discarded was never delivered mail.",
)
def discard_draft(message_id: str, mailbox: dict = Depends(require_mailbox)):
    try:
        with imap_session(mailbox["email"]) as conn:
            folder, uid = _resolve(conn, message_id, "Drafts")
            expunge_message(conn, folder, uid)
            return create_api_response("success", "Draft discarded")
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail=create_api_response("error", str(exc))
        ) from None
    except ImapUnavailableError as exc:
        raise _unavailable(exc) from None


# ---- One message --------------------------------------------------------


class MoveRequest(BaseModel):
    folder: str = Field(..., min_length=1, max_length=255)


def _load(conn, message_id: str) -> dict:
    folder, uid = _resolve(conn, message_id)
    fetched = fetch_raw_message(conn, folder, uid)
    if fetched is None:
        raise HTTPException(
            status_code=404, detail=create_api_response("error", "Message not found")
        )
    raw, flags = fetched
    return parse_message(raw, folder, uid, flags)


@router.get(
    "/messages/{message_id}",
    summary="Read one message",
    description=(
        "The full message with both body representations and attachment metadata. "
        "Fetched with BODY.PEEK, so opening a message does not mark it read -- the "
        "client says so explicitly via /mark-read, which is what makes 'mark as "
        "unread' mean anything."
    ),
)
def message_detail(message_id: str, mailbox: dict = Depends(require_mailbox)):
    try:
        with imap_session(mailbox["email"]) as conn:
            return create_api_response(
                "success", "Message retrieved successfully", _load(conn, message_id)
            )
    except ImapUnavailableError as exc:
        raise _unavailable(exc) from None


@router.get(
    "/messages/{message_id}/attachments",
    summary="List a message's attachments",
)
def message_attachments(message_id: str, mailbox: dict = Depends(require_mailbox)):
    try:
        with imap_session(mailbox["email"]) as conn:
            return create_api_response(
                "success",
                "Attachments retrieved successfully",
                _load(conn, message_id).get("attachments", []),
            )
    except ImapUnavailableError as exc:
        raise _unavailable(exc) from None


@router.get(
    "/messages/{message_id}/attachments/{index}",
    summary="Download one attachment",
    description="Streams the decoded part back with its own content type and filename.",
)
def message_attachment(message_id: str, index: int, mailbox: dict = Depends(require_mailbox)):
    try:
        with imap_session(mailbox["email"]) as conn:
            folder, uid = _resolve(conn, message_id)
            fetched = fetch_raw_message(conn, folder, uid)
            if fetched is None:
                raise HTTPException(
                    status_code=404,
                    detail=create_api_response("error", "Message not found"),
                )
            part = extract_attachment(fetched[0], index)
            if part is None:
                raise HTTPException(
                    status_code=404,
                    detail=create_api_response("error", "Attachment not found"),
                )

            # Content-Disposition is always "attachment", never "inline", even
            # for parts the message marked inline: this endpoint hands back
            # arbitrary sender-supplied bytes, and letting the browser render
            # them in the app's own origin is how an HTML attachment becomes
            # stored XSS. The reading pane displays inline images through the
            # sandboxed iframe instead (PRD section 7).
            return Response(
                content=part["content"],
                media_type=part["type"],
                headers={
                    "Content-Disposition": (
                        "attachment; filename*=UTF-8''" + quote(part["name"], safe="")
                    ),
                    "X-Content-Type-Options": "nosniff",
                },
            )
    except ImapUnavailableError as exc:
        raise _unavailable(exc) from None


@router.get(
    "/messages/{message_id}/raw",
    summary="Download the original message (RFC 822)",
    description=(
        "The stored bytes exactly as Dovecot holds them -- headers, MIME structure "
        "and encodings untouched, never a re-serialisation. What 'Show original' and "
        "'Download .eml' load, and the ground truth for any 'is the API dropping this "
        "header?' question. Fetched with BODY.PEEK, so it does not mark the message "
        "read."
    ),
)
def message_raw(message_id: str, mailbox: dict = Depends(require_mailbox)):
    try:
        with imap_session(mailbox["email"]) as conn:
            folder, uid = _resolve(conn, message_id)
            fetched = fetch_raw_message(conn, folder, uid)
            if fetched is None:
                raise HTTPException(
                    status_code=404,
                    detail=create_api_response("error", "Message not found"),
                )
            raw = fetched[0]
            # Always a download, never rendered: message/rfc822 can carry an
            # HTML part, and the attachment route's stored-XSS reasoning
            # applies unchanged. The filename is reduced to ASCII by
            # raw_download_name, so it needs no RFC 5987 encoding.
            return Response(
                content=raw,
                media_type="message/rfc822",
                headers={
                    "Content-Disposition": (
                        f'attachment; filename="{raw_download_name(raw, uid)}"'
                    ),
                    "X-Content-Type-Options": "nosniff",
                },
            )
    except ImapUnavailableError as exc:
        raise _unavailable(exc) from None


# ---- Flags and placement ------------------------------------------------


def _apply_flag(email: str, message_id: str, flag: str, value: bool):
    try:
        with imap_session(email) as conn:
            folder, uid = _resolve(conn, message_id)
            set_flag(conn, folder, uid, flag, value)
            return create_api_response("success", "Message updated")
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail=create_api_response("error", str(exc))
        ) from None
    except ImapUnavailableError as exc:
        raise _unavailable(exc) from None


# Each flag action gets its own literal path rather than sharing a
# `/{message_id}/{action}` catch-all.
#
# The catch-all version shipped briefly and broke /trash: FastAPI matches in
# declaration order, so `/{action}` claimed "trash" before the real handler was
# reached and answered "Unknown action" with a 404. That is the same ordering
# hazard the Laravel routes documented for "send", and a catch-all re-creates
# it every time a new sibling route is added. Literal paths cannot.


@router.post("/messages/{message_id}/mark-read", summary="Mark a message read")
def message_mark_read(message_id: str, mailbox: dict = Depends(require_mailbox)):
    return _apply_flag(mailbox["email"], message_id, "read", True)


@router.post("/messages/{message_id}/mark-unread", summary="Mark a message unread")
def message_mark_unread(message_id: str, mailbox: dict = Depends(require_mailbox)):
    return _apply_flag(mailbox["email"], message_id, "read", False)


@router.post("/messages/{message_id}/star", summary="Star a message")
def message_star(message_id: str, mailbox: dict = Depends(require_mailbox)):
    return _apply_flag(mailbox["email"], message_id, "starred", True)


@router.post("/messages/{message_id}/unstar", summary="Unstar a message")
def message_unstar(message_id: str, mailbox: dict = Depends(require_mailbox)):
    return _apply_flag(mailbox["email"], message_id, "starred", False)


@router.post(
    "/messages/{message_id}/move",
    summary="Move a message to another folder",
)
def message_move(message_id: str, body: MoveRequest, mailbox: dict = Depends(require_mailbox)):
    try:
        with imap_session(mailbox["email"]) as conn:
            folder, uid = _resolve(conn, message_id)
            new_id = move_message(conn, folder, uid, body.folder)
            return create_api_response("success", "Message moved", {"id": new_id})
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail=create_api_response("error", str(exc))
        ) from None
    except ImapUnavailableError as exc:
        raise _unavailable(exc) from None


@router.post(
    "/messages/{message_id}/trash",
    summary="Move a message to Trash",
    description="What the webmail's delete button calls. Recoverable by design.",
)
def message_trash(message_id: str, mailbox: dict = Depends(require_mailbox)):
    try:
        with imap_session(mailbox["email"]) as conn:
            folder, uid = _resolve(conn, message_id)
            trash = _trash_folder(conn)
            if folder == trash:
                # Already there. Trashing again must not silently destroy it --
                # permanent removal is DELETE, a separate deliberate act.
                return create_api_response(
                    "success", "Message is already in Trash", {"id": message_id}
                )
            return create_api_response(
                "success",
                "Message moved to Trash",
                {"id": move_message(conn, folder, uid, trash)},
            )
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail=create_api_response("error", str(exc))
        ) from None
    except ImapUnavailableError as exc:
        raise _unavailable(exc) from None


@router.delete(
    "/messages/{message_id}",
    summary="Permanently delete a message",
    description=(
        "Refused unless the message is ALREADY in Trash. PRD section 7 is binding: "
        "nothing outside Trash is ever destroyed, so reaching this requires a "
        "deliberate second delete from inside Trash."
    ),
)
def message_destroy(message_id: str, mailbox: dict = Depends(require_mailbox)):
    try:
        with imap_session(mailbox["email"]) as conn:
            folder, uid = _resolve(conn, message_id)
            trash = _trash_folder(conn)
            if folder != trash:
                raise HTTPException(
                    status_code=409,
                    detail=create_api_response(
                        "error",
                        "Move the message to Trash before deleting it permanently",
                        error_code="not_in_trash",
                    ),
                )
            expunge_message(conn, folder, uid)
            return create_api_response("success", "Message deleted permanently")
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail=create_api_response("error", str(exc))
        ) from None
    except ImapUnavailableError as exc:
        raise _unavailable(exc) from None


@router.get(
    "/messages/{message_id}/thread",
    summary="The conversation a message belongs to",
    description=(
        "Oldest first. Threading follows RFC 5322 Message-ID / In-Reply-To / "
        "References, never subject lines -- subject matching collapses unrelated "
        "'Re: hello' messages into one conversation."
    ),
)
def message_thread(message_id: str, mailbox: dict = Depends(require_mailbox)):
    try:
        with imap_session(mailbox["email"]) as conn:
            folder, uid = _resolve(conn, message_id)
            return create_api_response(
                "success", "Thread retrieved successfully", thread_for(conn, folder, uid)
            )
    except ImapUnavailableError as exc:
        raise _unavailable(exc) from None


@router.get(
    "/contacts",
    summary="Addresses this mailbox corresponds with",
    description=(
        "Harvested from message headers, most-used first, for compose "
        "autocomplete. There is no address book to fill in -- the mailbox already "
        "knows who its owner writes to."
    ),
)
def contacts(mailbox: dict = Depends(require_mailbox)):
    try:
        with imap_session(mailbox["email"]) as conn:
            names = {f["name"] for f in list_folders(conn)}
            # Sent first so a correspondent's display name, which outgoing mail
            # is more likely to carry, wins over a bare address seen inbound.
            scan = [f for f in ("Sent", "INBOX") if f in names]
            return create_api_response(
                "success", "Contacts retrieved successfully", harvest_contacts(conn, scan)
            )
    except ImapUnavailableError as exc:
        raise _unavailable(exc) from None


# ---------------------------------------------------------------------------
# Send (PRD Phase 3)
# ---------------------------------------------------------------------------

_ADDRESS_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def _addresses(raw: list[str] | None) -> list[str]:
    """Recipients that actually parse, de-duplicated, order preserved.

    Accepts the display-name form -- `Joel Confidence <joel@example.com>` --
    as well as bare addresses. Splitting on `[,;]` and regex-matching each
    piece rejected the former outright ("Add at least one valid recipient",
    which reads as though the user typed a bad address), and mangled any
    display name containing a comma: `"Omojefe, Joel" <j@example.com>` became
    two fragments, neither of them an address.

    email.utils.getaddresses handles the RFC 5322 grammar -- comma lists,
    quoted names, angle brackets -- so only the extracted address is
    validated. The display name is intentionally discarded: these values also
    become the SMTP envelope recipients, which must be bare addresses.
    """
    seen, out = set(), []
    # getaddresses does not understand `;` as a separator, so normalise the
    # ones outside quotes first -- some clients still emit it.
    normalised = [re.sub(r';(?=(?:[^"]*"[^"]*")*[^"]*$)', ",", v or "") for v in (raw or [])]
    for _display_name, address in getaddresses(normalised):
        address = address.strip()
        if not address or not _ADDRESS_RE.match(address):
            continue
        if address.lower() in seen:
            continue
        seen.add(address.lower())
        out.append(address)
    return out


class _JsonForm:
    """Reads a JSON body through the same surface as multipart form data.

    Send accepts both encodings: multipart when the message carries
    attachments, JSON when it does not (the webmail's own client picks per
    message, and a hand-written client will reach for JSON first). Rather
    than branch every field read, a JSON body is wrapped in this adapter so
    the handler below is written once against `.get()` / `.getlist()`.

    `getlist` accepts a bare string as a one-element list because
    `{"to": "a@b.c"}` is the mistake every client makes once, and rejecting
    it buys nothing. JSON integers come back as their string form, so
    `"attachment_indexes": [3, 5]` reads the same as the multipart field
    "3,5" -- bool is excluded because it is an int subclass in Python and
    `true` is never an index.
    """

    def __init__(self, data: object) -> None:
        self._data = data if isinstance(data, dict) else {}

    @staticmethod
    def _scalar(value: object) -> str | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, (str, int)):
            return str(value)
        return None

    def get(self, name: str) -> object:
        value = self._data.get(name)
        return value if isinstance(value, str) else None

    def getlist(self, name: str) -> list:
        value = self._data.get(name)
        if isinstance(value, list):
            return [s for s in (self._scalar(v) for v in value) if s is not None]
        scalar = self._scalar(value)
        return [scalar] if scalar is not None else []


# Tracking on mail a person sends to a person.
#
# Everything submitted through the internal listener passes the tracking
# content filter (mailer/postfix/scripts/tracking_injector.py), which injects
# an open pixel and rewrites links. Right for a campaign; wrong for a mailbox
# holder writing to a colleague, who never asked to be told when it was
# opened and whose recipient never agreed to a beacon. Mailbox sends
# therefore opt out per message with this header, which the filter honours
# and strips before delivery so it never reaches the recipient.
# MAILBOX_SEND_TRACKING=true turns tracking back on for a deployment that
# wants it. Default off.
TRACKING_OPT_OUT_HEADER = "X-Mailyte-Tracking"


def _mailbox_send_tracking_enabled() -> bool:
    return os.getenv("MAILBOX_SEND_TRACKING", "false").strip().lower() == "true"


def _shared_mailbox_permission(member_email: str, shared_email: str) -> str | None:
    """This member's permission on that shared mailbox, or None.

    The authority for sending as a shared address from the webmail. Postfix's
    smtpd_sender_login_maps answers the same question for SASL submission, and
    the two are kept deliberately in step: same table, same permission set,
    same requirement that BOTH mailboxes are active -- so suspending either
    one withdraws the right on every path at once.

    Returns None on any database error rather than raising. The caller treats
    that as "not permitted", which fails CLOSED: a send that should have been
    allowed is refused and retried, where the opposite would let one mailbox
    send as another.
    """
    connection = get_db_connection()
    if not connection:
        logger.error("Shared-mailbox permission check: no database connection")
        return None

    try:
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT smm.permission
            FROM shared_mailbox_members smm
            JOIN email_accounts shared
              ON shared.id = smm.shared_mailbox_id
             AND shared.mailbox_type = 'shared'
             AND shared.status = 'active'
            JOIN email_accounts member
              ON member.id = smm.email_account_id
             AND member.status = 'active'
            WHERE shared.email = %s AND member.email = %s
            """,
            (shared_email, member_email),
        )
        row = cursor.fetchone()
        cursor.close()
        return row["permission"] if row else None
    except Exception as exc:  # noqa: BLE001 -- must not leak into a 500
        logger.error("Shared-mailbox permission check failed: %s", exc)
        return None
    finally:
        connection.close()


def _parse_indexes(values: list) -> list[int]:
    """attachment_indexes as ints -- repeated fields, a JSON array, or "1,3"."""
    indexes: list[int] = []
    for value in values:
        for piece in str(value).split(","):
            piece = piece.strip()
            if not piece:
                continue
            if not piece.isdigit():
                raise HTTPException(
                    status_code=422,
                    detail=create_api_response("error", "attachment_indexes must be whole numbers"),
                )
            indexes.append(int(piece))
    return list(dict.fromkeys(indexes))


def _forwarded_parts(email: str, source_id: str, indexes: list[int]) -> list[dict]:
    """Attachment parts lifted from a message already in this mailbox.

    Opened as the session's own mailbox, so a source id from anyone else's
    mailbox resolves to nothing -- the same ownership rule every other route
    here gets from the session rather than from the id. Indexes are the
    ones GET /messages/{id} reported; a stale one is a 422 naming it, not a
    silently thinner forward.
    """
    with imap_session(email) as conn:
        folder, uid = _resolve(conn, source_id)
        fetched = fetch_raw_message(conn, folder, uid)
        if fetched is None:
            raise HTTPException(
                status_code=404,
                detail=create_api_response(
                    "error", "The message to forward attachments from was not found"
                ),
            )
        parts = []
        for index in indexes:
            part = extract_attachment(fetched[0], index)
            if part is None:
                raise HTTPException(
                    status_code=422,
                    detail=create_api_response(
                        "error", f"Attachment {index} was not found on the forwarded message"
                    ),
                )
            parts.append(part)
        return parts


@router.post(
    "/messages/send",
    summary="Send a message",
    description=(
        "Submits through Postfix and files a copy in Sent. The From address is "
        "always the signed-in mailbox: submission is trusted-network rather than "
        "SASL-authenticated, so Postfix's own sender-login check is bypassed and "
        "this endpoint is the only thing preventing one mailbox sending as another. "
        "Replies (`in_reply_to` set) flag the original \\Answered; forwards carry "
        "attachments over with `forward_attachments_from` + `attachment_indexes`."
    ),
)
async def send_message(
    request: Request,
    mailbox: dict = Depends(require_mailbox),
):
    """multipart/form-data when the message carries attachments, JSON when not.

    Both are accepted deliberately. `request.form()` returns an *empty*
    FormData for a JSON body rather than raising, so parsing only as a form
    made every attachment-less send fail with "Add at least one valid
    recipient" -- a validation error that reads as the caller's fault and
    hides the encoding mismatch entirely.

    `async def` here, unlike the rest of this router, because reading an
    uploaded file is genuinely async I/O; the blocking IMAP and SMTP work is
    handed to a worker thread below rather than run on the event loop.
    """
    content_type = (request.headers.get("content-type") or "").split(";")[0].strip()
    if content_type.lower() == "application/json":
        try:
            form = _JsonForm(await request.json())
        except Exception:
            raise HTTPException(
                status_code=422,
                detail=create_api_response("error", "Malformed JSON body"),
            )
    else:
        form = await request.form()

    def field(name: str) -> str | None:
        value = form.get(name)
        return value if isinstance(value, str) else None

    def repeated(name: str) -> list:
        """Values for a repeated field under either naming convention.

        The webmail sends `to[]`, `cc[]`, `bcc[]` and `attachments[]` -- PHP's
        array notation, because it was written against the Laravel route this
        replaces. A client written against this API from scratch would
        naturally send the bare name instead.

        Both are accepted. Requiring only the bare name silently breaks every
        existing send with "Add at least one valid recipient", and requiring
        only the bracketed form bakes a PHP-ism into an API that is about to
        be published for other people to write clients against.
        """
        return list(form.getlist(name)) + list(form.getlist(f"{name}[]"))

    to = _addresses(repeated("to"))
    cc = _addresses(repeated("cc"))
    bcc = _addresses(repeated("bcc"))
    if not to and not cc and not bcc:
        raise HTTPException(
            status_code=422,
            detail=create_api_response("error", "Add at least one valid recipient"),
        )

    subject = (field("subject") or "").strip()
    body_text = field("body_text")
    body_html = field("body_html")
    if not body_text and not body_html:
        raise HTTPException(
            status_code=422,
            detail=create_api_response("error", "The message has no content"),
        )

    uploads = [f for f in repeated("attachments") if isinstance(f, UploadFile)]

    # Attachments carried over from a message being forwarded. Uploading
    # them again from the client would mean downloading each one first,
    # which is how forwards came to arrive without their attachments; the
    # server already holds the original, so it lifts the parts itself.
    forward_from = (field("forward_attachments_from") or "").strip() or None
    indexes = _parse_indexes(repeated("attachment_indexes"))
    if indexes and not forward_from:
        raise HTTPException(
            status_code=422,
            detail=create_api_response(
                "error", "attachment_indexes needs forward_attachments_from"
            ),
        )
    forwarded: list[dict] = []
    if forward_from and indexes:
        try:
            forwarded = await run_in_threadpool(
                _forwarded_parts, mailbox["email"], forward_from, indexes
            )
        except ImapUnavailableError as exc:
            raise _unavailable(exc) from None

    # A forwarded inline image the new HTML still references by cid: goes
    # out as a related part under the same id, so it renders where it did in
    # the original. Anything else -- a real attachment, or an inline part the
    # body no longer mentions -- travels as an ordinary attachment rather
    # than being lost.
    inline_parts, carried = [], []
    for part in forwarded:
        cid = part.get("content_id")
        if cid and body_html and f"cid:{cid}" in body_html:
            inline_parts.append(
                {"cid": cid, "type": part["type"], "name": part["name"], "content": part["content"]}
            )
        else:
            carried.append({"name": part["name"], "type": part["type"], "content": part["content"]})

    if len(uploads) + len(forwarded) > MAX_ATTACHMENTS:
        raise HTTPException(
            status_code=422,
            detail=create_api_response(
                "error", f"At most {MAX_ATTACHMENTS} attachments per message"
            ),
        )

    attachments = []
    for upload in uploads:
        attachments.append(
            {
                "name": upload.filename or "attachment",
                "type": upload.content_type or "application/octet-stream",
                "content": await upload.read(),
            }
        )

    # One cap over uploads and carried-over parts together, checked before
    # base64 expansion, so the sender is told the message is too large
    # instead of Postfix rejecting it after the whole upload.
    total = 0
    for item in attachments + carried + inline_parts:
        total += len(item["content"])
        if len(item["content"]) > MAX_ATTACHMENT_BYTES or total > MAX_TOTAL_ATTACHMENT_BYTES:
            raise HTTPException(
                status_code=422,
                detail=create_api_response(
                    "error",
                    f"Attachments exceed {MAX_TOTAL_ATTACHMENT_BYTES // (1024 * 1024)} MB",
                ),
            )

    in_reply_to = (field("in_reply_to") or "").strip() or None
    # Optional: the API id (FOLDER:UID) of the message being replied to. Lets
    # the \Answered flag land without a search; verified against the
    # Message-ID before it is trusted.
    in_reply_to_id = (field("in_reply_to_id") or "").strip() or None

    # Sending AS a shared mailbox. The address is never taken on trust: it is
    # checked against shared_mailbox_members here because THIS endpoint is the
    # only gate. Postfix's smtpd_sender_login_maps (which does know about
    # shared mailboxes) governs SASL submission from Outlook and the like, and
    # is bypassed entirely by this path -- submit_message goes out over the
    # trusted network, so anything not verified here is not verified at all.
    send_as = (field("from") or "").strip().lower() or None
    on_behalf_of = None

    if send_as and send_as != mailbox["email"].lower():
        permission = _shared_mailbox_permission(mailbox["email"], send_as)

        if permission not in ("full_access", "send_as", "send_on_behalf"):
            raise HTTPException(
                status_code=403,
                detail=create_api_response(
                    "error", "You do not have permission to send from that address"
                ),
            )

        # Exchange's convention, which every major client renders as
        # "person on behalf of shared": the shared address owns From, the
        # person who actually pressed send is named in Sender.
        if permission == "send_on_behalf":
            on_behalf_of = mailbox["email"]
    else:
        send_as = None

    envelope_from = send_as or mailbox["email"]

    def deliver() -> dict:
        # From is the session's mailbox unless the caller asked to send as a
        # shared address AND was authorised above -- never straight from the
        # client. See submit_message: Postfix trusts this service and will not
        # check.
        message = build_message(
            from_address=envelope_from,
            to=to,
            cc=cc,
            bcc=bcc,
            subject=subject,
            body_text=body_text,
            body_html=body_html,
            in_reply_to=in_reply_to,
            references=field("references"),
            attachments=attachments + carried,
            # Pictures the editor embedded as data: URIs (a signature banner,
            # a pasted screenshot) go out as Content-ID parts. Gmail and
            # Outlook do not render data: images in received mail; every
            # client renders cid:. Drafts deliberately do NOT do this -- see
            # build_message.
            inline_images=True,
            inline_parts=inline_parts,
        )
        if on_behalf_of:
            message["Sender"] = on_behalf_of

        # Person-to-person mail is not tracked unless the deployment opts in.
        # The filter strips this header before delivery.
        if not _mailbox_send_tracking_enabled():
            message[TRACKING_OPT_OUT_HEADER] = "off"
        raw = message_bytes(message)
        # Envelope sender matches From so SPF and DMARC align on the shared
        # domain; a bounce then comes back to the shared mailbox, which is
        # where the team will look for it.
        submit_message(envelope_from, to + cc + bcc, raw)

        # Filed only after submission succeeded, so Sent never shows a message
        # that was never sent. The reverse -- sent but unfiled -- is the
        # survivable direction, and is logged rather than failed.
        sent_id = None
        answered_id = None
        try:
            with imap_session(mailbox["email"]) as conn:
                folders = list_folders(conn)
                sent = next((f["name"] for f in folders if f["role"] == "sent"), "Sent")
                # Bcc is stripped from the filed copy: it is already in the
                # envelope, and leaving the header in Sent discloses the blind
                # recipients to anyone who later opens that message.
                del message["Bcc"]
                # The opt-out is an instruction to our own filter, not part
                # of what the holder wrote; it has no place in their Sent copy.
                del message[TRACKING_OPT_OUT_HEADER]
                uid = append_message(conn, sent, message_bytes(message), flags="(\\Seen)")
                sent_id = compose_message_id(sent, uid) if uid else None

                # The original is flagged \Answered here because nothing else
                # can: the client never touches it, and without the flag the
                # mailbox records nothing as answered -- in any client.
                if in_reply_to:
                    hint = None
                    if in_reply_to_id:
                        hint_folder, hint_uid = split_message_id(in_reply_to_id)
                        if hint_uid.isdigit():
                            hint = (hint_folder or "INBOX", hint_uid)
                    answered_id = mark_answered(
                        conn,
                        in_reply_to,
                        [f["name"] for f in folders if f["role"] != "drafts"],
                        hint,
                    )
        except Exception as exc:
            logger.warning("Message sent but could not be filed in Sent: %s", exc)

        return {"id": sent_id, "message_id": message["Message-ID"], "answered_id": answered_id}

    try:
        result = await run_in_threadpool(deliver)
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail=create_api_response("error", str(exc))
        ) from None
    except ImapUnavailableError as exc:
        raise _unavailable(exc) from None

    return create_api_response("success", "Message sent", result)


# ---------------------------------------------------------------------------
# Settings (PRD Phase 4)
# ---------------------------------------------------------------------------

# Must match the choices the webmail's ComposingSettings renders. A value it
# cannot display is a setting the holder can save and never change back.
UNDO_SECONDS_CHOICES = (5, 10, 20, 30)
DEFAULT_UNDO_SECONDS = 5


class UpdateSettingsRequest(BaseModel):
    signature_html: str | None = None
    signature_on_reply: bool | None = None
    display_density: str | None = None
    undo_send_enabled: bool | None = None
    undo_send_seconds: int | None = None


def _preferences_row(cursor, email_account_id: str) -> dict:
    """The mailbox's preferences, creating the row on first read.

    Every column has a DB-side default (see 0016), so a freshly inserted row
    is complete. The Laravel version relied on application defaults and its
    firstOrCreate returned NULLs, which made `(int) null` a 0-second undo
    window -- a setting that silently did nothing.
    """
    cursor.execute(
        "SELECT * FROM mailbox_preferences WHERE email_account_id = %s",
        (email_account_id,),
    )
    row = cursor.fetchone()
    if row:
        return row

    cursor.execute(
        "INSERT IGNORE INTO mailbox_preferences (id, email_account_id) VALUES (%s, %s)",
        (generate_ulid(), email_account_id),
    )
    cursor.execute(
        "SELECT * FROM mailbox_preferences WHERE email_account_id = %s",
        (email_account_id,),
    )
    return cursor.fetchone()


def _settings_payload(mailbox: dict, row: dict, storage: dict) -> dict:
    return {
        "email_address": mailbox["email"],
        "name": mailbox.get("name"),
        "signature_html": row.get("signature_html") or "",
        "signature_on_reply": bool(row.get("signature_on_reply")),
        "display_density": row.get("display_density") or "comfortable",
        "undo_send_enabled": bool(row.get("undo_send_enabled")),
        "undo_send_seconds": int(row.get("undo_send_seconds") or DEFAULT_UNDO_SECONDS),
        "storage": storage,
    }


@router.get(
    "/settings",
    summary="The mailbox holder's own settings",
    description="Signature, display density, undo-send and real storage figures.",
)
def settings_show(mailbox: dict = Depends(require_mailbox)):
    conn = get_db_connection()
    if not conn:
        raise HTTPException(
            status_code=500,
            detail=create_api_response("error", "Database connection failed"),
        )
    cursor = conn.cursor(dictionary=True)
    try:
        row = _preferences_row(cursor, mailbox["email_account_id"])
        conn.commit()
    finally:
        cursor.close()
        conn.close()

    # Storage comes from the mail server, not the database -- a stored figure
    # is stale the moment a message arrives.
    try:
        with imap_session(mailbox["email"]) as imap:
            storage = quota(imap)
    except ImapUnavailableError:
        # Settings must still open when the mail server is unreachable; the
        # storage panel shows nothing rather than the whole page failing.
        storage = {"used_mb": 0, "quota_mb": 0, "percentage": None}

    return create_api_response(
        "success", "Settings retrieved successfully", _settings_payload(mailbox, row, storage)
    )


@router.put(
    "/settings",
    summary="Update the mailbox holder's settings",
    description=(
        "Partial update -- only the fields present are changed. The signature is "
        "sanitised against an allowlist before storage, because it is HTML this "
        "server later attaches to outgoing mail."
    ),
)
def settings_update(body: UpdateSettingsRequest, mailbox: dict = Depends(require_mailbox)):
    updates: dict = {}

    if body.signature_html is not None:
        try:
            updates["signature_html"] = sanitize_signature(body.signature_html)
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail=create_api_response("error", str(exc))
            ) from None
    if body.signature_on_reply is not None:
        updates["signature_on_reply"] = 1 if body.signature_on_reply else 0
    if body.display_density is not None:
        if body.display_density not in ("comfortable", "compact"):
            raise HTTPException(
                status_code=422,
                detail=create_api_response("error", "Unknown display density"),
            )
        updates["display_density"] = body.display_density
    if body.undo_send_enabled is not None:
        updates["undo_send_enabled"] = 1 if body.undo_send_enabled else 0
    if body.undo_send_seconds is not None:
        if body.undo_send_seconds not in UNDO_SECONDS_CHOICES:
            raise HTTPException(
                status_code=422,
                detail=create_api_response(
                    "error",
                    "Undo window must be one of "
                    + ", ".join(f"{c}s" for c in UNDO_SECONDS_CHOICES),
                ),
            )
        updates["undo_send_seconds"] = body.undo_send_seconds

    conn = get_db_connection()
    if not conn:
        raise HTTPException(
            status_code=500,
            detail=create_api_response("error", "Database connection failed"),
        )
    cursor = conn.cursor(dictionary=True)
    try:
        _preferences_row(cursor, mailbox["email_account_id"])
        if updates:
            assignments = ", ".join(f"{column} = %s" for column in updates)
            cursor.execute(
                f"UPDATE mailbox_preferences SET {assignments} WHERE email_account_id = %s",
                (*updates.values(), mailbox["email_account_id"]),
            )
        row = _preferences_row(cursor, mailbox["email_account_id"])
        conn.commit()
    finally:
        cursor.close()
        conn.close()

    try:
        with imap_session(mailbox["email"]) as imap:
            storage = quota(imap)
    except ImapUnavailableError:
        storage = {"used_mb": 0, "quota_mb": 0, "percentage": None}

    return create_api_response(
        "success", "Settings updated successfully", _settings_payload(mailbox, row, storage)
    )


# ---------------------------------------------------------------------------
# Security: two-factor and sign-in history (PRD Phase 4)
# ---------------------------------------------------------------------------

TOTP_SERVICE_URL = os.getenv("TOTP_SERVICE_URL", "http://totp:8103")

# What mailbox 2FA actually defends, stated honestly and surfaced in the API.
# It gates the WEBMAIL sign-in only: IMAP, SMTP and every desktop or phone
# client authenticate straight against Dovecot with the mailbox password and
# never see this. Claiming otherwise in the UI would be the fake-security
# pattern this codebase has already had to unwind once.
TWO_FACTOR_SCOPE = "webmail_sign_in_only"


def _totp_key(email: str) -> str:
    """The key this mailbox's TOTP secret is stored under.

    `totp_secrets.user_email` is UNIQUE with no tier column, and operators
    already use it. An operator whose address is also a hosted mailbox would
    otherwise share ONE secret across two credential tiers -- enrolling in the
    webmail would enrol the console, and disabling in one would disable both.
    Namespacing keeps them structurally separate while still reusing the one
    TOTP implementation, as mail-server phase-06 task 6.7 requires.

    TRACKED DEBT: the cleaner fix is a `scope` column on totp_secrets with a
    composite unique key. That means changing a table and a service the
    operator tier depends on, which is not a change to make in passing. The
    visible cost of the namespace is cosmetic: the authenticator app shows
    `Mailyte:mailbox:someone@example.com` as the account label.
    """
    return f"mailbox:{email}"


def _totp_call(path: str, payload: dict | None = None, method: str = "POST") -> dict:
    try:
        if method == "GET":
            response = requests.get(f"{TOTP_SERVICE_URL}{path}", timeout=10)
        else:
            response = requests.post(f"{TOTP_SERVICE_URL}{path}", json=payload, timeout=10)
    except requests.RequestException as exc:
        logger.error("TOTP service unreachable: %s", exc)
        raise HTTPException(
            status_code=502,
            detail=create_api_response("error", "Two-factor service unavailable"),
        ) from None
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    # The totp service answers {status, msg, data} -- its own envelope, not
    # this API's {type, msg, data}. Unwrap `data` here so callers below read
    # real fields instead of silently getting None for every one of them.
    return {
        "status": response.status_code,
        "body": payload.get("data") or {},
        "error": payload.get("msg") or payload.get("error"),
    }


def _qr_svg(otpauth_uri: str) -> str:
    code = qrcode.QRCode(box_size=8, border=2)
    code.add_data(otpauth_uri)
    buffer = io.BytesIO()
    code.make_image(image_factory=qrcode.image.svg.SvgPathImage).save(buffer)
    return buffer.getvalue().decode()


class TwoFactorCodeRequest(BaseModel):
    code: str = Field(..., min_length=4, max_length=32)


# The confirmation timestamp lives on email_accounts, not in totp_secrets
# (0021_mailbox_password_policy). totp_secrets.created_at is written by
# /totp/setup -- the enrolment START -- and its updated_at is ON UPDATE
# CURRENT_TIMESTAMP, so it moves on every sign-in verify; neither is "when
# the holder proved they had the code". The mobile team's §12: this endpoint
# used to report created_at as two_factor_confirmed_at, non-null the moment
# the QR was shown.


def _two_factor_confirmed_at(email_account_id: str) -> datetime | None:
    conn = get_db_connection()
    if not conn:
        return None
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            "SELECT two_factor_confirmed_at FROM email_accounts WHERE id = %s",
            (email_account_id,),
        )
        row = cursor.fetchone()
        return row["two_factor_confirmed_at"] if row else None
    finally:
        cursor.close()
        conn.close()


def _set_two_factor_confirmed_at(email_account_id: str, when: datetime | None) -> None:
    """Best-effort bookkeeping AFTER the totp service has already committed
    the real state change; a failure here is logged, never surfaced -- the
    enrolment is enabled (or disabled) regardless of whether we noted when."""
    conn = get_db_connection()
    if not conn:
        logger.error("Could not record two_factor_confirmed_at for %s", email_account_id)
        return
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            "UPDATE email_accounts SET two_factor_confirmed_at = %s WHERE id = %s",
            (when, email_account_id),
        )
        conn.commit()
    except Exception as exc:
        logger.error("Could not record two_factor_confirmed_at for %s: %s", email_account_id, exc)
    finally:
        cursor.close()
        conn.close()


@router.get(
    "/security",
    summary="The mailbox holder's security state",
    description=(
        "Two-factor status and what it protects. `protects` is part of the "
        "contract: mailbox 2FA gates the webmail sign-in only, never IMAP or SMTP, "
        "and the UI must not overclaim its reach."
    ),
)
def security_show(mailbox: dict = Depends(require_mailbox)):
    result = _totp_call(f"/totp/status/{quote(_totp_key(mailbox['email']), safe='')}", method="GET")
    status = result["body"] if result["status"] == 200 else {}
    enabled = bool(status.get("enabled"))
    # A row exists but /confirm has not succeeded: the QR was shown and
    # nothing more. The totp service reports the codes it generated at
    # /begin, but until /confirm they protect nothing, so they are not
    # "remaining" in any sense the holder cares about.
    pending = bool(status.get("enrolled")) and not enabled
    confirmed_at = _two_factor_confirmed_at(mailbox["email_account_id"]) if enabled else None
    return create_api_response(
        "success",
        "Security retrieved successfully",
        {
            "two_factor_enabled": enabled,
            "two_factor_confirmed_at": confirmed_at.isoformat() if confirmed_at else None,
            "two_factor_pending": pending,
            "recovery_codes_remaining": (
                int(status.get("backup_codes_remaining") or 0) if enabled else 0
            ),
            "protects": TWO_FACTOR_SCOPE,
        },
    )


@router.post(
    "/security/2fa/begin",
    summary="Start two-factor enrolment",
    description=(
        "Generates a secret and recovery codes. Enrolment is NOT active until "
        "/confirm succeeds with a working code -- otherwise a mis-scanned QR would "
        "lock the holder out of their own mailbox."
    ),
)
def two_factor_begin(mailbox: dict = Depends(require_mailbox)):
    result = _totp_call("/totp/setup", {"user_email": _totp_key(mailbox["email"])})
    if result["status"] not in (200, 201):
        raise HTTPException(
            status_code=400,
            detail=create_api_response("error", result["error"] or "Could not start enrolment"),
        )
    body = result["body"]
    return create_api_response(
        "success",
        "Enrolment started",
        {
            "secret": body.get("secret"),
            "qr_code_svg": _qr_svg(body.get("otpauth_uri") or ""),
            "recovery_codes": body.get("backup_codes") or [],
        },
    )


@router.post(
    "/security/2fa/confirm",
    summary="Confirm two-factor enrolment with a code",
)
def two_factor_confirm(body: TwoFactorCodeRequest, mailbox: dict = Depends(require_mailbox)):
    result = _totp_call(
        "/totp/enable", {"user_email": _totp_key(mailbox["email"]), "token": body.code}
    )
    if result["status"] != 200:
        raise HTTPException(
            status_code=422,
            detail=create_api_response("error", result["error"] or "That code did not match"),
        )
    # Now, and only now, is the enrolment confirmed. Recorded here because
    # this is the layer that knows /confirm succeeded; see the note above
    # _two_factor_confirmed_at for why the totp service cannot tell us.
    confirmed_at = datetime.now()
    _set_two_factor_confirmed_at(mailbox["email_account_id"], confirmed_at)
    return create_api_response(
        "success",
        "Two-factor authentication enabled",
        {"two_factor_enabled": True, "two_factor_confirmed_at": confirmed_at.isoformat()},
    )


@router.post(
    "/security/2fa/disable",
    summary="Turn two-factor off",
    description="Requires a current code, so a borrowed session cannot remove it.",
)
def two_factor_disable(body: TwoFactorCodeRequest, mailbox: dict = Depends(require_mailbox)):
    result = _totp_call(
        "/totp/disable", {"user_email": _totp_key(mailbox["email"]), "token": body.code}
    )
    if result["status"] != 200:
        raise HTTPException(
            status_code=422,
            detail=create_api_response("error", result["error"] or "That code did not match"),
        )
    _set_two_factor_confirmed_at(mailbox["email_account_id"], None)
    return create_api_response(
        "success", "Two-factor authentication disabled", {"two_factor_enabled": False}
    )


@router.get(
    "/security/sessions",
    summary="Where this mailbox is signed in",
)
def security_sessions(mailbox: dict = Depends(require_mailbox)):
    conn = get_db_connection()
    if not conn:
        raise HTTPException(
            status_code=500,
            detail=create_api_response("error", "Database connection failed"),
        )
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            "SELECT id, created_at, expires_at, revoked_at, ip_address, user_agent, "
            "       client_platform, idle_timeout_seconds "
            "FROM mailbox_sessions WHERE email_account_id = %s "
            "ORDER BY created_at DESC LIMIT 50",
            (mailbox["email_account_id"],),
        )
        rows = cursor.fetchall()
    finally:
        cursor.close()
        conn.close()

    now = datetime.now()
    return create_api_response(
        "success",
        "Sessions retrieved successfully",
        [
            {
                "id": row["id"],
                "signed_in_at": row["created_at"].isoformat() if row["created_at"] else None,
                "expires_at": row["expires_at"].isoformat() if row["expires_at"] else None,
                "ip_address": row["ip_address"],
                "user_agent": row["user_agent"],
                # What the client declared at sign-in (web|ios|android|macos|
                # windows|linux), null when it said nothing recognisable. The
                # idle window explains why a native session's expires_at sits
                # weeks out while a browser's sits hours out.
                "client_platform": row.get("client_platform"),
                "idle_timeout_seconds": row.get("idle_timeout_seconds"),
                "revoked": row["revoked_at"] is not None,
                "active": row["revoked_at"] is None and row["expires_at"] > now,
                # Marked so the UI can label it and refuse to offer "sign out
                # this device" for the session doing the asking.
                "current": row["id"] == mailbox["session_id"],
            }
            for row in rows
        ],
    )


@router.delete(
    "/security/sessions/{session_id}",
    summary="Sign out another device",
    description=(
        "Revokes one session. Refuses the caller's own -- signing yourself out "
        "from a list of other devices is almost always a misclick, and /logout is "
        "the deliberate way to do it."
    ),
)
def security_revoke_session(session_id: str, mailbox: dict = Depends(require_mailbox)):
    if session_id == mailbox["session_id"]:
        raise HTTPException(
            status_code=409,
            detail=create_api_response(
                "error",
                "Use sign out to end the session you are using",
                error_code="cannot_revoke_current",
            ),
        )
    conn = get_db_connection()
    if not conn:
        raise HTTPException(
            status_code=500,
            detail=create_api_response("error", "Database connection failed"),
        )
    cursor = conn.cursor(dictionary=True)
    try:
        # Scoped to this mailbox's own sessions, so an id belonging to someone
        # else's mailbox matches nothing rather than revoking their session.
        cursor.execute(
            "UPDATE mailbox_sessions SET revoked_at = NOW() "
            "WHERE id = %s AND email_account_id = %s AND revoked_at IS NULL",
            (session_id, mailbox["email_account_id"]),
        )
        affected = cursor.rowcount
        conn.commit()
    finally:
        cursor.close()
        conn.close()

    if not affected:
        raise HTTPException(
            status_code=404, detail=create_api_response("error", "Session not found")
        )
    return create_api_response("success", "Session signed out")


# ---------------------------------------------------------------------------
# AI assistance (PRD Phase 4 + mobile v1 section 13) -- optional, and gated
# ---------------------------------------------------------------------------

# Any OpenAI-compatible endpoint. Mailyte points these at Azure OpenAI; a
# self-hoster points them at their own key, a local model, or nothing.
#
# The credential decides whether AI EXISTS on this deployment: absent, the
# routes 503 ai_not_configured and /capabilities reports ai=false, so the
# webmail removes the buttons -- PRD section 7's "no control on screen that
# doesn't work". Whether a given MAILBOX may use it is three further gates --
# org policy, plan entitlement, individual consent -- resolved in order by
# utils/ai_consent.resolve_maya (mobile v1 13a-2/13e), plus a per-mailbox
# monthly quota. A webmail built before the consent flow now receives 403
# consent_required here, which is correct: consent cannot be implied by the
# client's age. Consent, history, documents, the thread summariser and the
# classifier live in routes/mailbox_ai.py under this same prefix.
AI_BASE_URL = os.getenv("AI_BASE_URL", "").strip().rstrip("/")
AI_API_KEY = os.getenv("AI_API_KEY", "").strip()
AI_MODEL = os.getenv("AI_MODEL", "gpt-4o-mini").strip()
AI_TIMEOUT = int(os.getenv("AI_TIMEOUT", "45"))

# Enough of a message to summarise without posting an entire newsletter to a
# third party, and enough to keep the request inside a modest context window.
AI_MAX_INPUT_CHARS = 12000


class AiComposeRequest(BaseModel):
    instruction: str = Field(..., min_length=1, max_length=2000)
    existing_draft: str | None = Field(None, max_length=20000)


def _require_ai() -> None:
    if not AI_BASE_URL or not AI_API_KEY:
        raise HTTPException(
            status_code=503,
            detail=create_api_response(
                "error",
                "No AI endpoint is configured for this deployment",
                error_code="ai_not_configured",
            ),
        )


def _maya(mailbox: dict = Depends(require_mailbox)) -> dict:
    """Mailbox session plus every Maya gate, in spec order: configured ->
    org policy -> entitlement -> consent. Raises with the OUTERMOST failing
    gate's error_code (503 ai_not_configured; 403 org_policy_blocked /
    not_entitled / consent_required / terms_version_stale).

    Local import, like _ai_configured's: routes/mailbox_ai.py imports this
    module, so keeping utils.ai_consent out of the shared top-of-file import
    block also keeps the dependency direction obvious.
    """
    from utils.ai_consent import resolve_maya

    return resolve_maya(mailbox)


def _ai_chat(system: str, user: str) -> str:
    _require_ai()
    try:
        response = requests.post(
            f"{AI_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {AI_API_KEY}",
                "api-key": AI_API_KEY,  # Azure OpenAI uses this header instead.
                "Content-Type": "application/json",
            },
            json={
                "model": AI_MODEL,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user[:AI_MAX_INPUT_CHARS]},
                ],
                "temperature": 0.4,
            },
            timeout=AI_TIMEOUT,
        )
    except requests.RequestException as exc:
        logger.error("AI endpoint unreachable: %s", exc)
        raise HTTPException(
            status_code=502,
            detail=create_api_response("error", "The AI service did not respond"),
        ) from None

    if response.status_code != 200:
        logger.error("AI endpoint returned %s: %s", response.status_code, response.text[:300])
        raise HTTPException(
            status_code=502,
            detail=create_api_response("error", "The AI service could not complete this"),
        )

    try:
        return response.json()["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, ValueError) as exc:
        logger.error("Unexpected AI response shape: %s", exc)
        raise HTTPException(
            status_code=502,
            detail=create_api_response("error", "The AI service returned nothing usable"),
        ) from None


@router.post(
    "/ai/compose",
    summary="Draft or rewrite a message with AI",
    description=(
        "503 error_code ai_not_configured when no endpoint is set; 403 "
        "org_policy_blocked / not_entitled / consent_required / "
        "terms_version_stale when a Maya gate fails; 429 ai_quota_exhausted when "
        "the monthly allowance is spent. Responses carry ai_calls_remaining."
    ),
)
def ai_compose(body: AiComposeRequest, maya: dict = Depends(_maya)):
    from utils.ai_consent import metered_ai_call, redact_for_ai

    prompt = f"Instruction: {body.instruction}"
    if body.existing_draft:
        prompt += f"\n\nCurrent draft to revise:\n{body.existing_draft}"
    with metered_ai_call(maya) as meter:
        draft = _ai_chat(
            "You write email. Reply with the message body only -- no subject line, "
            "no preamble, no explanation, no markdown fences.",
            redact_for_ai(prompt),
        )
    return create_api_response(
        "success",
        "Draft generated",
        {"draft": draft, "ai_calls_remaining": meter.remaining},
    )


@router.post(
    "/ai/summarize/{message_id}",
    summary="Summarise a message",
    description=(
        "Gated and metered exactly like /ai/compose. The whole-conversation "
        "variant is POST /ai/summarize/thread/{message_id} (routes/mailbox_ai.py)."
    ),
)
def ai_summarize(message_id: str, maya: dict = Depends(_maya)):
    from utils.ai_consent import metered_ai_call, redact_for_ai

    mailbox = maya["mailbox"]
    try:
        with imap_session(mailbox["email"]) as conn:
            message = _load(conn, message_id)
    except ImapUnavailableError as exc:
        raise _unavailable(exc) from None

    body_text = message.get("body_text") or message.get("body_html") or ""
    if not body_text.strip():
        raise HTTPException(
            status_code=422,
            detail=create_api_response("error", "This message has no text to summarise"),
        )

    # Charged only around the provider call itself: the IMAP fetch above and
    # the empty-body 422 cost the holder nothing.
    with metered_ai_call(maya) as meter:
        summary = _ai_chat(
            "Summarise the email in at most three sentences. State any action the "
            "reader is being asked to take. Add nothing that is not in the message.",
            redact_for_ai(f"Subject: {message.get('subject')}\n\n{body_text}"),
        )
    return create_api_response(
        "success",
        "Summary generated",
        {"summary": summary, "ai_calls_remaining": meter.remaining},
    )


# ---------------------------------------------------------------------------
# Rules, forwarding and the vacation responder (PRD Phase 5)
# ---------------------------------------------------------------------------


class ForwardingRequest(BaseModel):
    enabled: bool = False
    addresses: list[str] = Field(default_factory=list)
    keep_copy: bool = True


class RulesRequest(BaseModel):
    rules: list[dict] = Field(default_factory=list)


class VacationRequest(BaseModel):
    enabled: bool = False
    subject: str | None = Field(None, max_length=500)
    message: str | None = Field(None, max_length=20000)
    start_date: str | None = Field(None, max_length=40)
    end_date: str | None = Field(None, max_length=40)


# The blocked-senders component (routes/mailbox_blocked_senders.py). Named
# here, not there, because that module imports this one and the active
# script below has to include it.
BLOCKED_SENDERS_SCRIPT = "mailyte-blocked-senders"


def _sieve_unavailable(*, configured: bool | None = None) -> HTTPException:
    """503 with a stable error_code the webmail can branch on.

    ``sieve_not_configured``: no master credential on this deployment --
    permanent until an operator sets one. ``sieve_unavailable``: there is a
    credential but ManageSieve could not be reached or logged into just now
    -- transient, retry later. Neither is ever a 502: nothing the mailbox
    holder sends can change either, and 502 reads as "the API is broken".
    """
    if configured is None:
        configured = _sieve_credentials_configured()
    if not configured:
        return HTTPException(
            status_code=503,
            detail=create_api_response(
                "error",
                "Mail filtering is not configured on this deployment",
                error_code="sieve_not_configured",
            ),
        )
    return HTTPException(
        status_code=503,
        detail=create_api_response(
            "error",
            "Mail filtering is temporarily unavailable. Try again in a minute.",
            error_code="sieve_unavailable",
        ),
    )


def _compile_active_script() -> str:
    """compile_main() with the blocked-senders component included FIRST.

    Blocked senders must run before rules, forwarding and vacation: a blocked
    message must neither be forwarded on nor draw an auto-reply, and ``stop``
    in an included script ends the whole run (RFC 6609 section 3.2), so
    putting it first is what makes "blocked" mean blocked. compile_main() in
    utils/sieve_compilers.py knows only its original three components; the
    include is spliced in here rather than that list edited, and it is
    ``:optional`` like the others so a mailbox that has never blocked anyone
    still compiles.
    """
    main = compile_main()
    include = f'include :personal :optional "{BLOCKED_SENDERS_SCRIPT}";\n'
    if include in main:
        return main
    lines = main.splitlines(keepends=True)
    for index, existing in enumerate(lines):
        if existing.startswith("include "):
            lines.insert(index, include)
            return "".join(lines)
    return main + include


def _read_script(email: str, name: str) -> str | None:
    """The named component script, or None when the mailbox has never saved one.

    Gated on the credential, not on the cached probe: a real attempt is a
    better answer than a 60-second-old one, and its outcome is fed back so the
    capability flips off (and back on) with what actually happened.
    """
    from utils import managesieve

    if not _sieve_credentials_configured():
        raise _sieve_unavailable(configured=False)
    try:
        with ManageSieveClient(email) as sieve:
            content = sieve.get_script(name)
    except managesieve.ManageSieveUnavailableError as exc:
        logger.error("ManageSieve unavailable reading %s/%s: %s", email, name, exc)
        managesieve.report_failure(str(exc))
        raise _sieve_unavailable(configured=True) from None
    except ManageSieveError as exc:
        logger.error("ManageSieve read failed for %s/%s (%s): %s", email, name, exc.code, exc)
        raise HTTPException(
            status_code=502,
            detail=create_api_response("error", "Could not read mail filters"),
        ) from None
    managesieve.report_success()
    return content


def _write_script(email: str, name: str, content: str) -> None:
    """Store a component script and make sure the including script is active.

    The component is stored INACTIVE. Activating it would deactivate the other
    two -- Sieve has one active script -- which is the bug this replaces.
    """
    from utils import managesieve

    if not _sieve_credentials_configured():
        raise _sieve_unavailable(configured=False)
    try:
        with ManageSieveClient(email) as sieve:
            sieve.put_script(name, content)
            sieve.put_script(MAIN_SCRIPT, _compile_active_script())
            sieve.set_active(MAIN_SCRIPT)
    except managesieve.ManageSieveUnavailableError as exc:
        logger.error("ManageSieve unavailable writing %s/%s: %s", email, name, exc)
        managesieve.report_failure(str(exc))
        raise _sieve_unavailable(configured=True) from None
    except ManageSieveError as exc:
        message = str(exc)
        code = exc.code or ""
        logger.error("ManageSieve write failed for %s/%s (%s): %s", email, name, code, message)
        if code == "TRYLATER":
            raise _sieve_unavailable(configured=True) from None
        # A script the Sieve compiler refuses is a 400 with its own diagnostic:
        # the caller can act on "line 3: unknown command", and cannot act on
        # "something went wrong". Same for a quota (QUOTA/MAXSIZE,
        # QUOTA/MAXSCRIPTS): the response code is the reliable signal, the
        # wording checks are the pre-existing fallback for servers without one.
        lowered = message.lower()
        if code.startswith("QUOTA") or "refused" in lowered or "line" in lowered:
            raise HTTPException(
                status_code=400, detail=create_api_response("error", message)
            ) from None
        raise HTTPException(
            status_code=502,
            detail=create_api_response("error", "Could not save mail filters"),
        ) from None
    managesieve.report_success()


@router.get("/forwarding", summary="Mail forwarding settings")
def forwarding_show(mailbox: dict = Depends(require_mailbox)):
    parsed = parse_forwarding(_read_script(mailbox["email"], FORWARDING_SCRIPT))
    return create_api_response("success", "Forwarding retrieved successfully", parsed)


@router.put(
    "/forwarding",
    summary="Update mail forwarding",
    description=(
        "Backed by Sieve `redirect :copy`, not a Postfix alias -- the alias route "
        "delivers two copies of every forwarded message."
    ),
)
def forwarding_update(body: ForwardingRequest, mailbox: dict = Depends(require_mailbox)):
    addresses = clean_addresses(body.addresses, exclude=mailbox["email"])
    if body.enabled and not addresses:
        raise HTTPException(
            status_code=422,
            detail=create_api_response(
                "error",
                "Add an address to forward to, or turn forwarding off. "
                "A mailbox cannot forward to itself.",
            ),
        )
    if len(addresses) > MAX_FORWARD_ADDRESSES:
        raise HTTPException(
            status_code=422,
            detail=create_api_response(
                "error", f"You can forward to at most {MAX_FORWARD_ADDRESSES} addresses"
            ),
        )

    settings = {
        "enabled": body.enabled,
        "addresses": addresses,
        "keep_copy": body.keep_copy,
    }
    _write_script(mailbox["email"], FORWARDING_SCRIPT, compile_forwarding(settings))
    return create_api_response("success", "Forwarding saved", {**settings, "managed": True})


@router.get("/rules", summary="The mailbox's filter rules")
def rules_show(mailbox: dict = Depends(require_mailbox)):
    parsed = parse_rules(_read_script(mailbox["email"], RULES_SCRIPT))
    return create_api_response("success", "Rules retrieved successfully", parsed)


@router.put("/rules", summary="Replace the mailbox's filter rules")
def rules_update(body: RulesRequest, mailbox: dict = Depends(require_mailbox)):
    if len(body.rules) > MAX_RULES:
        raise HTTPException(
            status_code=422,
            detail=create_api_response("error", f"At most {MAX_RULES} rules"),
        )
    _write_script(mailbox["email"], RULES_SCRIPT, compile_rules(body.rules))
    return create_api_response("success", "Rules saved", parse_rules(compile_rules(body.rules)))


@router.get("/vacation", summary="The vacation responder")
def vacation_show(mailbox: dict = Depends(require_mailbox)):
    parsed = parse_vacation(_read_script(mailbox["email"], VACATION_SCRIPT))
    return create_api_response("success", "Vacation retrieved successfully", parsed)


@router.put(
    "/vacation",
    summary="Update the vacation responder",
    description=(
        "Replies at most once per sender per day, and only to mail actually "
        "addressed to this mailbox -- a message merely Cc'd to a list does not "
        "trigger one."
    ),
)
def vacation_update(body: VacationRequest, mailbox: dict = Depends(require_mailbox)):
    if body.enabled and not (body.message or "").strip():
        raise HTTPException(
            status_code=422,
            detail=create_api_response(
                "error", "Write the message to send, or turn the responder off"
            ),
        )
    settings = body.model_dump()
    _write_script(mailbox["email"], VACATION_SCRIPT, compile_vacation(settings, mailbox["email"]))
    return create_api_response("success", "Vacation responder saved", {**settings, "managed": True})
