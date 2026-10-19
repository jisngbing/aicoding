#!/usr/bin/env bash
# Run from a verified release staging directory; preserve all persistent state.
set -Eeuo pipefail
TARGET=/opt/aicoding
STAGE=$(pwd -P)
RELEASE=$(python3 -c 'import json; print(json.load(open("release.json"))["release_id"])')
[[ "$STAGE" == "$TARGET/releases/$RELEASE" ]] || { echo "Unexpected staging path"; exit 1; }
[[ "$RELEASE" =~ ^[0-9]{8}T[0-9]{6}Z$ ]] || exit 1
BACKUP="$TARGET/backups/$RELEASE.tar.gz"
ROLLBACK="aicoding-backend:rollback-$RELEASE"
IMAGE="aicoding-backend:release-$RELEASE"
PROMOTED=0
mkdir -p "$TARGET/backups"
chmod 700 "$TARGET/backups"

rollback() {
    status=$?
    trap - ERR
    if [[ "$PROMOTED" == 1 ]]; then
        echo "Update failed; restoring previous application release"
        set +e
        docker tag "$ROLLBACK" aicoding-backend:linux
        tar -xzf "$BACKUP" -C "$TARGET"
        cd "$TARGET"
        docker compose -f deploy/compose.yml up -d --no-deps --force-recreate backend web
    fi
    exit "$status"
}
trap rollback ERR

python3 - <<'PY'
import hashlib, json
from pathlib import Path
manifest = json.loads(Path('release.json').read_text())
for name, expected in manifest['sha256'].items():
    path = Path(name)
    assert not path.is_absolute() and '..' not in path.parts
    assert hashlib.sha256(path.read_bytes()).hexdigest() == expected, name
print('Release manifest verified')
PY
docker compose -f "$TARGET/deploy/compose.yml" config --quiet
docker tag aicoding-backend:linux "$ROLLBACK"
BACKUP_FILES=()
for name in agent scripts ui/dist deploy requirements.txt requirements.lock.txt .dockerignore .env-linux release.json; do
    [[ ! -e "$TARGET/$name" ]] || BACKUP_FILES+=("$name")
done
tar -czf "$BACKUP" -C "$TARGET" "${BACKUP_FILES[@]}"
chmod 600 "$BACKUP"
docker build -t "$IMAGE" -f deploy/Dockerfile .
docker run --rm --entrypoint python "$IMAGE" -m compileall -q agent

PROMOTED=1
# Apply only the explicitly supplied search configuration. Never rotate site login or MongoDB settings.
if [[ -f search-settings.json ]]; then
    python3 - <<'PY'
import json, os
from pathlib import Path
path = Path('/opt/aicoding/.env-linux')
settings = json.loads(Path('search-settings.json').read_text())
allowed = {'ZHIPU_API_KEY', 'ZHIPU_SEARCH_ENGINE', 'ZHIPU_SEARCH_TIMEOUT'}
assert set(settings) <= allowed
lines = path.read_text().splitlines()
for key, value in settings.items():
    assert isinstance(value, str) and '\n' not in value and '\r' not in value and "'" not in value
    lines = [line for line in lines if line.split('=', 1)[0].strip() != key]
    lines.append(f"{key}='{value}'")
path.write_text('\n'.join(lines) + '\n')
os.chmod(path, 0o600)
Path('search-settings.json').unlink()
print('Search configuration updated')
PY
fi
for directory in agent scripts deploy; do
    mkdir -p "$TARGET/$directory"
    cp -a "$STAGE/$directory/." "$TARGET/$directory/"
done
cp requirements.txt requirements.lock.txt .dockerignore release.json "$TARGET/"
docker tag "$IMAGE" aicoding-backend:linux
cd "$TARGET"
docker compose -f deploy/compose.yml up -d --no-deps --force-recreate backend
HEALTHY=0
for attempt in $(seq 1 40); do
    CONTAINER=$(docker compose -f deploy/compose.yml ps -q backend)
    STATUS=$(docker inspect --format '{{.State.Health.Status}}' "$CONTAINER")
    if [[ "$STATUS" == healthy ]]; then HEALTHY=1; break; fi
    sleep 3
done
[[ "$HEALTHY" == 1 ]]
# Keep old hashed assets so cached pages remain usable during the transition.
mkdir -p ui/dist
cp -a "$STAGE/ui/dist/." ui/dist/
docker compose -f deploy/compose.yml exec -T web nginx -t
docker compose -f deploy/compose.yml up -d --no-deps --force-recreate web
docker compose -f deploy/compose.yml exec -T web nginx -t
docker compose -f deploy/compose.yml exec -T backend python -c 'from agent.core.persistence import get_mongo_client; get_mongo_client().admin.command("ping"); print("MongoDB connection verified")'
echo "DEPLOYMENT_OK release=$RELEASE backup=$BACKUP rollback=$ROLLBACK"
docker compose -f deploy/compose.yml ps
