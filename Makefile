.PHONY: verify-system test migrate-roundtrip

PYTHON ?= .venv/bin/python
SRC := 03-development/src

migrate-roundtrip:
	@$(PYTHON) -c "import os, sys; sys.path.insert(0, os.path.join(os.getcwd(), '$(SRC)')); from taskq.repository.tasks import reset_db; reset_db(); print('migrate-roundtrip: PASS')"

test:
	@cd 03-development && ../$(PYTHON) -m pytest tests/ -q --tb=short --no-header

verify-system: migrate-roundtrip test
	@echo "verify-system: PASS"
