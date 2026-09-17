install:
	pip install -e .

lint:
	ruff check easyget tests
	ruff format --check easyget tests

format:
	ruff format easyget tests

test:
	python -m unittest -v

package:
	python -m build

upload:
	twine upload dist/*

clean:
	rm -rf build dist *.egg-info
