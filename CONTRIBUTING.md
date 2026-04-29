# Contributing to SL

## Reporting bugs

Open an issue at https://github.com/robruon/sl/issues with:
- The `.sl` file that triggered the bug (or a minimal reproduction)
- The error message or unexpected output
- Your OS and Python version (`python3 --version`)

## Running the examples locally

```bash
git clone https://github.com/robruon/sl
cd sl
./install.sh
sl example.sl --run
sl advanced.sl --run
sl modules.sl --run
```

## Running the ARC runtime tests

```bash
cd arc
make        # debug build + address sanitizer tests
make tsan   # thread sanitizer
```

## Submitting a fix or feature

1. Fork the repo and create a branch
2. Make your change
3. Verify all three examples still pass:
   ```bash
   python codegen.py example.sl --run
   python codegen.py advanced.sl --run
   python codegen.py modules.sl --run
   ```
4. Open a pull request with a clear description of what changed and why

## Project layout

See the **Project Layout** section in README.md.

## Publishing a package

To add a package to the public registry, see:
https://github.com/robruon/sl-registry/blob/main/CONTRIBUTING.md
