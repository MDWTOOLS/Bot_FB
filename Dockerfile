# ============================================
#  Pterodactyl Panel - Railway Deployment
# ============================================
# Services: Panel (PHP) + MySQL 9 + Redis
#
# Railway Setup:
# 1. New Project
# 2. New Service → Provision MySQL
# 3. New Service → Provision Redis
# 4. New Service → Deploy this repo (Panel)
# 5. Set env vars (see .env.example)
# ============================================

# Panel Service
FROM ghcr.io/pterodactyl/panel:latest

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 80

ENTRYPOINT ["/entrypoint.sh"]
