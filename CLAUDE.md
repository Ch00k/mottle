# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Mottle is a Django web application that provides enhanced Spotify features including playlist management, artist tracking, and event discovery. It's built with Django 5.1, uses uv for dependency management, and runs in Docker containers.

## Development Commands

### Testing
```bash
make test                    # Run all tests using uv
uv run pytest web/tests     # Run web app tests directly
uv run pytest tests/        # Run utility tests
```

### Code Quality
```bash
# Run via pre-commit (recommended)
pre-commit run --all-files

# Run individually
ruff check .     # Linting
ruff format .    # Formatting  
mypy .          # Type checking
```

### Docker Development
```bash
make build_dev        # Build development Docker image
make up              # Start all services (web + task runners)
make down            # Stop all services
make logs            # View container logs
make ssh             # SSH into web container
make shell           # Django shell in container
make makemigrations  # Create new migrations
make debug           # Attach to running web service for debugging
```

### Django Management
```bash
# Standard Django commands (run in container)
docker compose exec web ./manage.py migrate
docker compose exec web ./manage.py createsuperuser

# Custom management commands
docker compose exec web ./manage.py get_event_updates
docker compose exec web ./manage.py get_playlist_updates
docker compose exec web ./manage.py track_artists_events
docker compose exec web ./manage.py create_feature_flag
```

## Architecture Overview

### Core Applications
- **web/**: Main application with Spotify integration, playlist management, event tracking
- **taskrunner/**: Background task coordination using Django-Q2
- **featureflags/**: Feature flag management system
- **urlshortener/**: URL shortening functionality
- **django_q_sentry/**: Custom Sentry integration for task queue

### Key Technologies
- **Django 5.1** with GeoDjango for spatial features
- **uv** for dependency management
- **Django-Q2** for background task processing with separate clusters (default, long_running)
- **Tekore** for Spotify API integration
- **HTMX** for dynamic frontend interactions
- **Docker Compose** with multi-service architecture

### Database Setup
- **Primary DB**: SpatiaLite (SQLite with geographic extensions) for main application data
- **Tasks DB**: Separate SQLite database for Django-Q2 task management
- **Dual routing**: Custom database routers separate task data from application data

### Background Tasks
- **Default cluster**: Short-running tasks (5min timeout)
- **Long-running cluster**: Extended tasks like playlist updates (20hr timeout)
- **Scheduler**: Optional cron-based task scheduling
- **Sentry integration**: Error reporting for failed tasks

### Host Configuration
- **Primary host**: Main application (`mottle.urls`)
- **URL shortener host**: Separate subdomain (`urlshortener.urls`)
- **Django-hosts**: Multi-host routing configuration

### Key Models
- **User**: Links to SpotifyUser with notification preferences and location
- **SpotifyAuth**: Encrypted token storage with automatic refresh
- **Playlist/PlaylistUpdate**: Playlist tracking with change history
- **Event**: Geographic event data with artist matching
- **FeatureFlag**: Runtime feature toggling

### Authentication Flow
- Spotify OAuth2 with encrypted token storage
- Custom middleware for automatic token refresh
- Scope management for different Spotify permissions

### Testing Strategy
- **Pytest** with Django integration
- **Factory Boy** for test data generation
- Separate test modules in `web/tests/` and `tests/`
- MyPy type checking with strict configuration

### Deployment
- **Multi-stage Docker builds** with separate dev/prod configurations
- **Environment-based configuration** using environs
- **Make targets** for building, tagging, and deploying releases
- **Database backups** created automatically during deployments

## Important Notes

- All Spotify tokens are encrypted at rest using Fernet encryption
- Geographic features use SRID 4326 (WGS84) coordinate system
- Background tasks handle API rate limiting and error recovery
- Feature flags control rollout of new functionality
- Pre-commit hooks enforce code quality standards automatically