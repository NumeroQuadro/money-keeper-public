COMPOSE=docker compose
PYTHON?=python3
RUFF?=ruff
MYPY?=mypy

.PHONY: up down restart logs ps api-shell bot-shell build test lint format typecheck metrics-audit

up:
	$(COMPOSE) up --build -d

build:
	$(COMPOSE) build

down:
	$(COMPOSE) down

restart:
	$(COMPOSE) down
	$(COMPOSE) up --build -d

logs:
	$(COMPOSE) logs -f --tail=100

ps:
	$(COMPOSE) ps

api-shell:
	$(COMPOSE) exec api bash

bot-shell:
	$(COMPOSE) exec bot bash

test:
	cd api && $(PYTHON) -m unittest discover -s app/tests -p "test_*.py"
	PYTHONPATH=. $(PYTHON) -m unittest discover -s bot/tests -p "test_*.py"

lint:
	cd api && $(RUFF) check app

format:
	cd api && $(RUFF) format app

typecheck:
	cd api && $(MYPY)

metrics-audit:
	$(PYTHON) scripts/metrics_audit.py
