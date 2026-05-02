#!/bin/bash
set -e

# Wait for MySQL to be ready
echo "[BOOT] Waiting for MySQL at $DB_HOST:$DB_PORT..."
max_tries=30
count=0
while ! nc -z "${DB_HOST:-127.0.0.1}" "${DB_PORT:-3306}" 2>/dev/null; do
    count=$((count + 1))
    if [ $count -ge $max_tries ]; then
        echo "[BOOT] MySQL connection timeout after ${max_tries}s"
        break
    fi
    sleep 2
done
echo "[BOOT] MySQL connection check done (took ${count}x2s)"

# Create .env from environment variables
cd /var/www/html

if [ ! -f .env ]; then
    echo "[BOOT] Creating .env from environment..."
    cp .env.example .env
fi

# Update .env with Railway env vars
if [ -n "$DB_HOST" ]; then
    sed -i "s/DB_HOST=.*/DB_HOST=${DB_HOST}/" .env
    sed -i "s/DB_PORT=.*/DB_PORT=${DB_PORT:-3306}/" .env
    sed -i "s/DB_DATABASE=.*/DB_DATABASE=${DB_DATABASE:-pterodactyl}/" .env
    sed -i "s/DB_USERNAME=.*/DB_USERNAME=${DB_USERNAME:-pterodactyl}/" .env
    sed -i "s/DB_PASSWORD=.*/DB_PASSWORD=${DB_PASSWORD}/" .env
fi

if [ -n "$REDIS_HOST" ]; then
    sed -i "s/REDIS_HOST=.*/REDIS_HOST=${REDIS_HOST}/" .env
    sed -i "s/REDIS_PORT=.*/REDIS_PORT=${REDIS_PORT:-6379}/" .env
    sed -i "s/CACHE_DRIVER=.*/CACHE_DRIVER=redis/" .env
    sed -i "s/SESSION_DRIVER=.*/SESSION_DRIVER=redis/" .env
    sed -i "s/QUEUE_CONNECTION=.*/QUEUE_CONNECTION=redis/" .env
fi

if [ -n "$APP_URL" ]; then
    sed -i "s|APP_URL=.*|APP_URL=${APP_URL}|" .env
fi

if [ -n "$APP_KEY" ]; then
    sed -i "s/APP_KEY=.*/APP_KEY=${APP_KEY}/" .env
else
    echo "[BOOT] Generating APP_KEY..."
    php artisan key:generate --force --no-interaction 2>/dev/null || true
fi

if [ -n "$MAIL_FROM" ]; then
    sed -i "s/MAIL_FROM_ADDRESS=.*/MAIL_FROM_ADDRESS=${MAIL_FROM}/" .env
fi

# Setup permissions
chown -R www-data:www-data /var/www/html
chmod -R 755 /var/www/html/storage
chmod -R 755 /var/www/html/bootstrap/cache

# Run migrations
echo "[BOOT] Running database migrations..."
php artisan migrate --force --no-interaction 2>/dev/null || echo "[BOOT] Migration skipped or failed (might need manual setup)"

# Start PHP-FPM and Nginx
echo "[BOOT] Starting Panel..."
php-fpm -R &
nginx -g 'daemon off;'
