# GPE Gdansk provider (experimental)

Gdansk accounts sign in through `https://uonetplus.edu.gdansk.pl/gdansk/`,
which uses a different login flow and API from eduVULCAN. Select `gdansk` in
the add-on's **Portal dziennika** option to use this provider. The default is
still `eduvulcan`, including installations without a `provider` setting.

Enter the GPE username and password in the add-on options. The account login
shown in the registration email can differ from the secondary email address.
The optional `student_name` filter matches the exact first and last name shown
in the diary; leave it empty to include all current-year school diaries.
Kindergarten diaries are not supported by this provider.

The browser completes the portal's normal login and hands the session to a
read-only HTTP client. It does not register a mobile device, solve CAPTCHA
challenges or accept agreements. Interactive account steps must be completed
on the portal first. No additional add-on permissions are needed.

## Data and cards

The provider returns grades for all classification periods, the previous,
current and next timetable weeks, attendance from the past 14 days, attendance
statistics, upcoming assignments from four weeks, remarks, achievements,
meetings and message headers. It preserves descriptions within those windows
and uses Vultron's existing sensor names and card schemas.

Point grades such as `6p` are preserved. An average is included only when GPE
exposes it. The grade sensor state is the total grade count, rather than
eduVULCAN's newly detected grade count; grade-count notification examples need
to account for this difference. The lucky number and per-subject attendance
statistics are not implemented. Missing month, semester and annual values
display a dash in the statistics card.

The messages card combines the root sensor's `wiadomosci` with sensors listed
in its `page_entities`, keeping large mailboxes below HA's attribute limit.
Its `limit` option still applies after sorting all pages. Headers link to the
GPE web inbox for reading and replying. The provider neither fetches bodies
nor marks messages as read, and it does not send messages or excuse absences.
Existing eduVULCAN message bodies continue to open in the local preview.

Automations reading `wiadomosci` directly see only the first page. When using
large mailboxes, include `page_entities` to cover all headers. Extra pages
are published before the root sensor; the root lists only current pages.
The root state is the unread count and its `total` attribute is the mailbox
size. Mailboxes are matched to students by name; ambiguous matches fail
explicitly instead of assigning one student's messages to another.

## Synchronization and diagnostics

The first synchronization runs immediately except during the 01:00–06:00
night pause. Later runs wait 40–60 minutes between collections, including
weekends. Test mode bypasses the night pause. A failed login or expired API
session stops this provider; other failures add a bounded retry delay.
Each cycle opens a short-lived Chromium session. Session reuse and receiving
school push notifications are not implemented.

`sensor.vultron_gdansk_status` reports `ok`, `partial`, `error` or
`authentication_error`. Failed sections retain previously published data.
Oversized non-message sections are reported in the status sensor instead of
being silently truncated; partitioning those sections is future work.
Credentials and session cookies are not included in diagnostics. As with the
existing provider, upstream's opt-in `trace` mode can log school response data.

Only verified HTTPS GPE origins receive session headers. API redirects are
not followed, and the student API has an explicit read endpoint allowlist.
All included response fixtures are public Wulkanowy test data, with attribution
and their Apache 2.0 license in `tests/fixtures/gdansk`.

## Verification

Install `vultron/requirements.txt` and `pytest` in a virtual environment, then
run `python -m pytest -q tests`. The browser card tests use Node's built-in
runner: `node --test tests/test_messages_card.cjs`. Neither suite needs a
school account or network access. The GPE test workflow runs both suites.

The provider was validated against GPE 26.06 on HAOS 18.3 / Home Assistant
2026.9.3 using a separately installed local build. Live checks covered grades,
three timetable weeks, attendance, assignments, school sections and a large
mailbox with the diagnostic status `ok`. The additional native message-card
support is covered by isolated behavior tests. No real school records or
credentials are included in this contribution.

When testing a local build, stop any other Vultron instance using the same
entity names and resource paths. Compare data with the portal before enabling
startup, and keep the previous build for rollback. After rebuilding cards
without changing the add-on version, change the resource URL's version query
under Settings > Dashboards > Resources and reload HA to avoid cached assets.
