# Darktable Python Library

The aim of this project is the following:

- Provide a Python library
  that allows programmatic access to your Darktable library
- Provide a way to programmatically export photos from your Darktable library
- Form the foundation for creating a web API
  to access and export Darktable images
- ...

## Setup

```sh
python -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
pip install -r requirements.txt
```

## Check for XMP inconsistencies

```
python database_inconsistencies.py /path/to/your/darktable/config
```

The first argument must be the path to your Darktable config directory
that contains `library.db` and `data.db`.
The script opens these library's in read-only mode (`file:{db_path}?mode=ro`),
no data is modified.

Relevant Darktable issue:
https://github.com/darktable-org/darktable/issues/15330
