# Contributing to article-fact-check

Thank you for your interest in contributing to **`article-fact-check`**! We welcome bug reports, feature suggestions, documentation enhancements, and pull requests.

---

## 🛠️ Development Setup

1. Work from a local checkout of the repository.

2. Create a virtual environment and install in editable mode with development dependencies:
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\Activate.ps1
   pip install -e ".[dev,pdf]"
   ```

---

## 🧪 Running Tests

Ensure all automated unit tests pass before submitting a pull request:

```bash
python -m unittest discover -s tests -v
```

The CI matrix targets Python 3.9 through 3.13.

---

## 📐 Guidelines

- **Zero-dependency philosophy**: Keep the core auditing engine in `audit_tool.py` dependent **only** on the Python standard library. Any heavy third-party packages must be optional extras.
- **Maintain Protocol Integrity**: If modifying `audit.json` schema fields, update `references/data-and-output-contract.md` and the validation tests. The current supported schema is 2.0.
- **Cross-Platform Compatibility**: Test paths and encodings across Windows, Linux, and macOS. Write generated files as UTF-8; preserve explicit decoding support for legacy inputs.

---

## 📝 Pull Request Process

1. Create a feature branch (`git checkout -b feature/my-feature`).
2. Add unit tests for your changes in `tests/test_audit_tool.py`.
3. Commit your changes with clear, descriptive commit messages.
4. Push to your branch and open a Pull Request.

---

## 📄 License

By contributing to `article-fact-check`, you agree that your contributions will be licensed under the project's [MIT License](LICENSE).
