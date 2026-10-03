.PHONY: install setup run test build
install: ; pip install -r requirements.txt
setup: ; cd backend && python -m app.setup
run: ; cd backend && uvicorn app.main:app --reload --port 8000
test: ; cd backend && python -m pytest -q tests
build: ; python tools/build.py
