install:
	pip install -e .

package:
	python setup.py sdist bdist_wheel

upload:
	twine upload dist/*

clean:
	rm -rf build dist *.egg-info
