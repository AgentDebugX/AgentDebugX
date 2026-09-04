"""The GUI package targets pydantic v2 (PathLike fields, model_validate); under the
pydantic v1 compatibility matrix these modules cannot even be collected
(`RuntimeError: no validator found for <class 'os.PathLike'>`). Skip the GUI suite there
instead of failing the whole job; the core library keeps its v1 coverage."""
from __future__ import annotations

import pydantic

if str(getattr(pydantic, 'VERSION', '2')).startswith('1'):
    collect_ignore_glob = ['test_*.py']
