#!/bin/sh
# Backend entrypoint: run database migrations, then start the application.

set -e

echo "Running database migrations..."

max_retries=30
retry=0
while ! /app/.venv/bin/alembic upgrade head 2>&1; do
    retry=$((retry + 1))
    if [ "$retry" -ge "$max_retries" ]; then
        echo "ERROR: Database migrations failed after $max_retries attempts. Starting application anyway."
        break
    fi
    echo "Database not ready, retrying in 2 seconds... (attempt $retry/$max_retries)"
    sleep 2
done

if [ "$retry" -lt "$max_retries" ]; then
    echo "Migrations completed successfully."
fi

exec "$@"
