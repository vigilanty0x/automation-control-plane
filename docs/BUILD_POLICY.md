# Build backend policy

The package metadata declares the audited compatibility window `setuptools>=83.0.0,<84`.

Continuous integration deliberately installs the exact reviewed toolchain version `setuptools==83.0.0` together with the pinned `build` and `wheel` versions before running `python -m build --no-isolation`. This separates two concerns:

- package consumers may build with any reviewed setuptools release inside the declared compatibility window;
- repository evidence remains reproducible because CI resolves one exact toolchain.

A change to either the compatibility window or the CI lock requires all of the following evidence on the same commit:

1. the complete unit and adversarial test suite passes on every supported Python version;
2. the repository boundary checker passes;
3. a wheel builds without dependency resolution drift;
4. the installed wheel CLI and the module example both pass their smoke tests;
5. the reason for the toolchain update is recorded in the pull request.

The build backend is not a runtime dependency. Runtime code remains standard-library-only.
