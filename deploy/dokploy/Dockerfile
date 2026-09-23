# SIH26123 fleet dashboard — the whole runtime is stdlib Python (see backend/server.py's
# own module docstring), so there is nothing to pip install. Build context is the repo
# root (see docker-compose.yml / Dokploy app build path = repo root, dockerfile =
# deploy/dokploy/Dockerfile).
FROM python:3.13-slim

WORKDIR /app

# Copy only what the dashboard actually serves + the sim/backend packages it imports.
# (Skips tests/, docs/, artifacts/, .git — keeps the image small and the build fast.)
COPY src/ ./src/
COPY backend/ ./backend/
COPY frontend/ ./frontend/
COPY models/ ./models/
COPY edge_node.py ./edge_node.py
COPY deploy/dokploy/run.py ./deploy/dokploy/run.py

EXPOSE 8000

CMD ["python", "deploy/dokploy/run.py"]
