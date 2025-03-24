from setuptools import setup, find_packages

setup(
    name="easyget",
    version="1.0.0",
    packages=find_packages(),
    install_requires=[
        "httpx>=0.27.0",
        "tqdm>=4.60.0"
    ],
    entry_points={
        "console_scripts": [
            "easyget=easyget.__main__:main",
        ]
    },
    author="Your Name",
    description="Fast, easy-to-use multi-platform file downloader",
    classifiers=[
        "Programming Language :: Python :: 3",
        "Operating System :: OS Independent",
    ],
    python_requires=">=3.7",
)
