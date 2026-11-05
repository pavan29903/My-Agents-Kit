# Edge-case checklist

Pick every category that can plausibly break the change; skip the rest
explicitly in the change document ("not applicable: no concurrency").

## Inputs
- Empty: `""`, `[]`, `{}`, zero rows, empty file, whitespace only.
- Missing / null: absent field, `None`/`null`/`undefined`, missing header or env var.
- Boundaries: 0, 1, -1, max length, max size, off-by-one around limits, first/last page.
- Wrong type or format: string for number, malformed JSON, bad date, invalid email/phone.
- Unicode and text: non-ASCII names (Telugu, emoji), RTL, very long strings, leading/trailing spaces, case differences.
- Duplicates: same item twice, same request twice (idempotency), duplicate keys.
- Large inputs: big files, many items, deep nesting; check time and memory stay reasonable.

## State and time
- Not found, already deleted, already exists, stale version.
- Time zones (IST vs UTC), DST, midnight and month/year boundaries, leap day, expiry exactly at the limit.
- Ordering: unsorted input, ties, pagination stability.

## Failure paths
- Dependency errors: database down, timeout, 4xx/5xx from an API, rate limit, partial failure in a batch.
- Retries do not duplicate side effects (emails, payments, messages).
- Error messages are clear and do not leak secrets or stack traces to users.

## Security and access
- Unauthenticated, wrong user, other tenant's data, expired token, role without permission.
- Injection: SQL, shell, path traversal (`../`), HTML/JS in user text.
- Uploads: wrong type, oversized, disguised extension.

## Concurrency (when shared state exists)
- Two requests at once on the same record; double-submit; race between read and write.

## UI (when a screen changed)
- Loading, empty, error, and success states; long text overflow; mobile width; keyboard only; slow network.

Record each covered case with its result in the change document's edge-case table.
