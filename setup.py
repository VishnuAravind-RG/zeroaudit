from setuptools import setup, find_packages

setup(
    name="zeroaudit",
    version="1.0.0",
    packages=find_packages(exclude=["tests*"]),
    python_requires=">=3.10",
)