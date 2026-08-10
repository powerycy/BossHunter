# BOSS Login Guard Design

## Goal

Expose the BOSS Zhipin login state in the dashboard and prevent a user from
starting collection, AI scoring, or delivery when the connected browser is not
logged in to BOSS.

## Scope

- Add a backend login-status endpoint backed by Browser Runtime diagnostics and
  an authenticated-page check.
- Refresh the login state in the dashboard header every 60 seconds.
- Show a clear signed-in, signed-out, or unavailable state beside the local
  service status.
- Reject workbench task starts and confirmed-delivery requests while the BOSS
  login guard is not ready.
- Preserve queued and unfinished jobs; the guard never deletes jobs or sends
  messages automatically.

## Login State

The backend owns the state calculation. It requires all of the following:

1. Browser Runtime is running and connected to Google Chrome.
2. A BOSS Zhipin browser tab is available.
3. The BOSS page does not present a login route or authenticated-session
   markers associated with a signed-out page.

The endpoint returns a stable payload with `ready`, `status`, `message`, and
`checked_at`. `status` is one of `logged_in`, `logged_out`, or `unavailable`.
The response contains no cookies, page content, API credentials, or browser
debugging data.

## Backend Guard

Introduce one login-guard helper used by both workbench entry points:

- `POST /api/workbench/task` blocks collection, score/rescore, full, monitor,
  and direct delivery task starts.
- `POST /api/workbench/deliver` blocks confirmation and queueing before it can
  update a job to `approved`.

The task-start endpoint performs the guard before creating a background task.
The delivery endpoint performs the same check before changing job state. A
failed check returns HTTP 400 with a direct instruction to open BOSS Zhipin in
the connected Chrome session and log in.

Existing running AI calls are not forcibly cancelled if a subsequent 60-second
status refresh finds a logout. This avoids abandoning an already submitted AI
request. Later task starts and delivery attempts remain blocked until login is
restored.

## Dashboard

The Header fetches the login status on mount and every 60 seconds. It displays:

- Green: BOSS logged in.
- Amber: BOSS is not logged in; open the connected Chrome session to log in.
- Red: Browser Runtime or Chrome is unavailable.

The existing local-service indicator remains visible independently so a healthy
web server is never confused with an authenticated BOSS session.

## Delivery Diagnosis

The investigated deployment had a delivery task that generated greetings for
five selected jobs and sent one. A user pause request then stopped the task and
preserved the remaining four jobs for a later send. This behavior is retained;
the dashboard continues to show the task log and pending greeting count.

## Tests

- Unit-test login-state classification for authenticated, signed-out, missing
  BOSS tab, and unavailable browser cases.
- API-test task and delivery routes reject before task creation or job-state
  changes when logged out.
- Regression-test the header polling and its three user-visible states.
- Run the full Python suite and frontend production build.
