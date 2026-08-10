# Resumable Workbench Task Cards Design

## Goal

Give a paused task card a safe continue action and a dismiss action without
deleting jobs, scores, greetings, or delivery history.

## Continue

When a task starts, the runner stores a private resume descriptor containing
its mode and original selected job IDs where applicable. The task snapshot
exposes whether it can be resumed, but never exposes internal configuration,
credentials, or browser state.

For a terminal task stopped by the user, the card shows a continue button:

- Delivery resumes only the original task's unsent job IDs.
- Score resumes only the original selected IDs; an unscoped score task resumes
  the normal pending-score queue.
- Collection, rescore, monitor, and full tasks start a fresh run of the same
  mode. Existing jobs remain deduplicated and completed scores remain stored.

The continue endpoint re-runs normal BOSS login and task preflight checks. It
cannot run while another task is active. It does not attempt to resume a task
that failed or completed normally.

Older in-memory task records that lack a resume descriptor do not receive a
continue button. This avoids guessing which jobs a historic delivery should
send.

## Dismiss

The dismiss action is available only for terminal task cards. It deletes the
in-memory task record and removes its card from the dashboard. It does not
modify the jobs database or related history. Active tasks must first be paused
or stopped before they can be dismissed.

## UI

The task card presents icon buttons with tooltips:

- Play: continue a user-paused task.
- Trash: dismiss a terminal task card.
- Pause remains available only while a task is active.

The card updates immediately after either action and displays route errors in
the existing task notice area.

## Tests

- Runner tests prove a stopped task exposes only safe resume metadata and can
  be dismissed only after reaching a terminal status.
- API tests prove continue preserves delivery IDs and runs through normal
  startup guards, while dismiss leaves job rows untouched.
- Frontend regression tests verify the card renders continue and dismiss
  controls only for the valid state.
- Run the full Python suite and frontend production build.
