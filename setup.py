from setuptools import setup, find_packages

setup(
    name="zeroaudit",
    version="2.0.0",
    description="Post-quantum zero-knowledge financial audit pipeline",
    packages=find_packages(exclude=["tests", "tests.*"]),
    python_requires=">=3.10",
)
