# Database Migrations

This directory contains database migration scripts managed by Alembic.

## Usage

### Create a new migration
```bash
alembic revision --autogenerate -m "Description of changes"
```

### Apply migrations
```bash
alembic upgrade head
```

### Rollback migrations
```bash
alembic downgrade -1
```

### View migration history
```bash
alembic history
```

## Directory Structure
- `versions/` - Contains all migration scripts
- `env.py` - Alembic environment configuration
- `script.py.mako` - Template for new migration scripts