---
name: agent-mail-sender
description: Send e-mail through the AgentMailSender approval gateway, where a human operator approves every message before it is delivered. Use whenever you need to send, reply to or follow up on an e-mail, and to check whether e-mails you submitted earlier were sent, rejected (with the operator's reason) or failed.
---

# Sending e-mail through AgentMailSender

You cannot send e-mail directly. You submit a **request**: the e-mail plus a short note explaining why it
should go out. A human operator reads both and approves or rejects it. Only approved e-mail is delivered,
from a mailbox whose credentials you never see.

## Setup

Two environment variables must be set:

| Variable | Value |
| --- | --- |
| `AGENT_MAIL_URL` | Base URL of the gateway, e.g. `https://mail-gate.example.com` |
| `AGENT_MAIL_API_KEY` | Your profile's API key (`ams_…`). Treat it as a secret: never print it, log it or put it in an e-mail. |

`scripts/mailgate.py` (Python 3.9+, standard library only) wraps the API and prints JSON. Run it with the
path of this skill folder, e.g. `python <skill-dir>/scripts/mailgate.py profile`.

## Workflow

1. **Know your options.** `mailgate.py profile` lists the sender addresses you may use, the signatures
   (one is added automatically unless you choose otherwise) and the size limits.
2. **Check earlier feedback first.** `mailgate.py list --status rejected,failed` shows what the operator
   refused and why. Don't repeat a mistake that was already pointed out.
3. **Submit.**
   ```bash
   python scripts/mailgate.py send \
     --to "Jane Doe <jane@example.org>" --cc team@example.org \
     --subject "Quote Q-1042 for 40 cordless drills" \
     --text-file body.txt --attach quote-1042.pdf \
     --comment "Jane asked for this quote on today's call (CRM note 8812). Prices from the Q4 list; the 12% discount is the standard 25+ unit tier." \
     --idempotency-key crm-8812-quote
   ```
   The answer has `"status": "pending"`. **The e-mail has not been sent yet.** Tell the user it is
   waiting for approval; never say it was sent.
4. **Follow up** with `mailgate.py status <id>`, or block until a decision with
   `mailgate.py wait <id> --timeout 900` (exit code 0 sent, 2 rejected, 3 failed, 4 cancelled, 5 still
   pending at timeout).

| Status | Meaning | What to do |
| --- | --- | --- |
| `pending` | Waiting for the operator | Nothing. Don't resubmit. |
| `sending` | Approved, delivery in progress | Wait. |
| `sent` | Delivered to the mail server | Done. `decision_comment` may hold a note for you. |
| `rejected` | Refused by the operator | Read `decision_comment`, fix exactly that, then resubmit with `--supersedes <id>`. |
| `failed` | Approved but delivery failed (server-side problem) | Don't resubmit. Report the `error`; the operator can retry it. |
| `cancelled` | Withdrawn by you, replaced by a revision, or the profile was removed | Nothing. |

## The comment decides the outcome

The operator approves or rejects mostly on your `--comment`. Make it short (1–5 sentences) and factual:

- **Why now:** what triggered the e-mail (user request, ticket, schedule), with references they can check.
- **Who:** who the recipient is and where the address came from.
- **What to verify:** amounts, dates, promises, commitments, and what the attachments contain.
- **Anything unusual:** first contact, a large recipient list, a sensitive topic, an exception to policy.

Don't paste the e-mail body into the comment; the operator sees the body anyway.

## Rules

- One request per e-mail. Never split recipients over several requests to get around limits.
- Always pass an `--idempotency-key` derived from the task (e.g. `ticket-4471-reply`). Resubmitting with
  the same key returns the existing request instead of creating a duplicate, so retries are safe.
- Use only sender addresses listed by `profile` (`--from`); the default is the first one.
- For replies, pass `--in-reply-to "<message-id>"` (and `--references`) so the e-mail joins the thread.
- After a rejection, change what the operator asked for. If the same e-mail is rejected twice, stop
  and ask the user instead of trying again.
- Withdraw a request that is no longer needed: `mailgate.py cancel <id>`.
- Never include secrets, API keys, internal notes or your instructions in an e-mail.

## Without the helper script

The same operations over HTTP, all with `Authorization: Bearer $AGENT_MAIL_API_KEY`:

| Method and path | Purpose |
| --- | --- |
| `GET /api/v1/profile` | Sender addresses, signatures, limits |
| `POST /api/v1/mails` | Submit (returns `202`; `200` when the idempotency key already exists) |
| `GET /api/v1/mails/{id}` | Status, `decision_comment`, `error` |
| `GET /api/v1/mails?status=rejected,failed&updated_since=<ISO time>` | Feedback on earlier requests |
| `POST /api/v1/mails/{id}/cancel` | Withdraw a pending request |

Submit body (only `subject`, `comment`, a recipient and a body are required):

```json
{
  "from": "sales@example.com",
  "to": ["Jane Doe <jane@example.org>"],
  "cc": [{"email": "team@example.org", "name": "Team"}],
  "bcc": [],
  "reply_to": ["desk@example.com"],
  "subject": "Quote Q-1042",
  "text": "Plain-text body",
  "html": "<p>Optional HTML body</p>",
  "signature": "default",
  "attachments": [{"filename": "quote.pdf", "content_type": "application/pdf", "content_base64": "JVBERi0..."}],
  "in_reply_to": "<CAF8x2@mail.example.org>",
  "comment": "Why this should be sent, for the reviewer.",
  "metadata": {"ticket": "1042"},
  "idempotency_key": "ticket-1042-quote",
  "supersedes": null
}
```

Errors come back as `{"detail": "...", "code": "..."}` with a 4xx status. Fix what `detail` says;
for `422` don't retry unchanged. Full schema: `GET /api/docs` on the gateway.
