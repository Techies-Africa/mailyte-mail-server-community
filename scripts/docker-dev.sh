#!/bin/bash
# Development Docker management script
# Usage: ./scripts/docker-dev.sh [up|down|restart|logs]

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ACTION=${1:-up}

case $ACTION in
    "up")
        echo "Starting development environment with hot-reloading..."
        docker compose -f "$PROJECT_ROOT/docker-compose.yml" -f "$PROJECT_ROOT/docker-compose.dev.yml" up -d
        echo "Development environment started!"
        ;;
    "down")
        echo "Stopping development environment..."
        docker compose -f "$PROJECT_ROOT/docker-compose.yml" -f "$PROJECT_ROOT/docker-compose.dev.yml" down
        echo "Development environment stopped!"
        ;;
    "restart")
        echo "Restarting development environment..."
        docker compose -f "$PROJECT_ROOT/docker-compose.yml" -f "$PROJECT_ROOT/docker-compose.dev.yml" down
        docker compose -f "$PROJECT_ROOT/docker-compose.yml" -f "$PROJECT_ROOT/docker-compose.dev.yml" up -d
        echo "Development environment restarted!"
        ;;
    "logs")
        docker compose -f "$PROJECT_ROOT/docker-compose.yml" -f "$PROJECT_ROOT/docker-compose.dev.yml" logs -f
        ;;
    *)
        echo "Usage: $0 [up|down|restart|logs]"
        ;;
esac
