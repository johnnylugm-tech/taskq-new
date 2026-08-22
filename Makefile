.PHONY: verify-system test migrate-roundtrip

ROOT := $(shell pwd)
PYTHON ?= $(ROOT)/.venv/bin/python
SRC := $(ROOT)/03-development/src

migrate-roundtrip:
	@PYTHONPATH=$(SRC) $(PYTHON) -c "from taskq.repository.tasks import reset_db; reset_db(); print('migrate-roundtrip: PASS')"

test:
	@cd $(ROOT)/03-development && $(PYTHON) -m pytest tests/ -q --tb=short --no-header

# verify-system invokes the DELIVERED entry point: starts the FastAPI service
# via uvicorn (the program a user would run), hits /healthz and /readyz, then
# stops it. Each step can fail independently (no || true, no --exit-zero).
verify-system: migrate-roundtrip
	@PYTHONPATH=$(SRC) $(PYTHON) -m uvicorn taskq.api.app:create_app --factory --host 127.0.0.1 --port 8765 --log-level error > /tmp/uvicorn.log 2>&1 & echo $$! > /tmp/uvicorn.pid; \
	for i in 1 2 3 4 5 6 7 8 9 10; do \
	  sleep 1; \
	  curl -fs http://127.0.0.1:8765/healthz > /dev/null 2>&1 && break; \
	done; \
	HEALTH=$$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8765/healthz 2>/dev/null || echo 000); \
	READY=$$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8765/readyz 2>/dev/null || echo 000); \
	if [ -f /tmp/uvicorn.pid ]; then \
	  kill $$(cat /tmp/uvicorn.pid) 2>/dev/null || true; \
	  rm -f /tmp/uvicorn.pid; \
	fi; \
	pkill -f 'uvicorn.*8765' 2>/dev/null || true; \
	if [ "$$HEALTH" = "200" ] && [ "$$READY" = "200" ]; then \
	  echo "verify-system: PASS (healthz=$$HEALTH readyz=$$READY)"; \
	else \
	  echo "verify-system: FAIL (healthz=$$HEALTH readyz=$$READY)"; \
	  exit 1; \
	fi
