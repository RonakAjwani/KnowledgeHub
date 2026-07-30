#!/bin/sh
# Migrations run here rather than as a separate Compose step or a manual
# post-`up` command: there is only ever one backend service (750 free
# instance-hours fits exactly one), so there is no separate place for a
# migration step to live on Render, and Compose has to mirror that shape
# rather than invent a second one just for local dev. `alembic upgrade head`
# is idempotent - already-applied revisions are a no-op - so this is safe to
# run on every container start, not just the first.
set -e

alembic upgrade head
exec "$@"
