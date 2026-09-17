install:
	pip install -e .

package:
	python -m build

upload:
	twine upload dist/*

clean:
	rm -rf build dist *.egg-info
