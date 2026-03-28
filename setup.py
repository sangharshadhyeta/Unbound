from setuptools import setup, find_packages

setup(
    name="unbound",
    version="0.1.0",
    packages=find_packages(),
    install_requires=[
        "fastapi>=0.111.0",
        "uvicorn[standard]>=0.29.0",
        "click>=8.1.7",
        "sqlalchemy>=2.0.30",
        "websockets>=12.0",
        "requests>=2.31.0",
    ],
    entry_points={
        "console_scripts": [
            "unbound=unbound.cli.cli:cli",
        ],
    },
)
