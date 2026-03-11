.PHONY: install dev test dashboard clean

install:
	pip install -e .

dev:
	pip install -e ".[dev]"

test:
	pytest tests/ -v

dashboard:
	python -m trouter dashboard

clean:
	rm -rf build dist *.egg-info
	find . -name __pycache__ -exec rm -rf {} +
