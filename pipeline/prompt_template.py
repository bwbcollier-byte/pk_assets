"""Deterministic Prompt Text template matching 21st.dev's 'Copy prompt' format.

Used by harvest_21st.py (full fidelity: main + demo + tailwind + registry deps)
and detect_new_components.py (simpler: main file only, deps inferred from
imports). Replaces the LLM-generated prompt text — this template matches
exactly what users paste into their Claude Code / v0 / Cursor sessions.
"""
from __future__ import annotations

import json
import re
from typing import Iterable

PREAMBLE = """You are given a task to integrate an existing React component in the codebase

The codebase should support:
- shadcn project structure
- Tailwind CSS
- Typescript

If it doesn't, provide instructions on how to setup project via shadcn CLI, install Tailwind or Typescript.

Determine the default path for components and styles.
If default path for components is not /components/ui, provide instructions on why it's important to create this folder
Copy-paste this component to /components/ui folder:"""

BOILERPLATE = """Implementation Guidelines
 1. Analyze the component structure and identify all required dependencies
 2. Review the component's argumens and state
 3. Identify any required context providers or hooks and install them
 4. Questions to Ask
 - What data/props will be passed to this component?
 - Are there any specific state management requirements?
 - Are there any required assets (images, icons, etc.)?
 - What is the expected responsive behavior?
 - What is the best place to use this component in the app?

Steps to integrate
 0. Copy paste all the code above in the correct directories
 1. Install external dependencies
 2. Fill image assets with Unsplash stock images you know exist
 3. Use lucide-react icons for svgs or logos if component requires them"""


_IMPORT_RE = re.compile(
    r"""^\s*import\s+(?:[^'"]*?\s+from\s+)?['"]([^'"]+)['"]""",
    re.MULTILINE,
)

# Peer / implicit deps that 21st.dev omits from its NPM install line because
# they're always already present in a shadcn project.
_SKIP_DEPS = {"react", "react-dom", "next", "tailwindcss", "typescript"}


def extract_npm_deps(code: str) -> list[str]:
    """Pull importable package names out of a source blob.

    Keeps bare specifiers and @scope/pkg ones, skips relative imports,
    internal aliases (@/...), and well-known peer dependencies. Preserves
    first-seen order.
    """
    seen: set[str] = set()
    out: list[str] = []
    for match in _IMPORT_RE.finditer(code):
        spec = match.group(1)
        if spec.startswith(".") or spec.startswith("@/") or spec.startswith("/"):
            continue
        if spec.startswith("@"):
            parts = spec.split("/", 2)
            pkg = "/".join(parts[:2]) if len(parts) >= 2 else spec
        else:
            pkg = spec.split("/", 1)[0]
        if not pkg or pkg in _SKIP_DEPS or pkg in seen:
            continue
        seen.add(pkg)
        out.append(pkg)
    return out


def _tailwind_js(config: dict) -> str:
    """Render a tailwind.config dict as a JS module export."""
    body = json.dumps(config, indent=2)
    # Unquote simple identifier keys for readability. Leaves keys with hyphens /
    # dots / any non-identifier character alone (still valid JS).
    body = re.sub(r'^(\s*)"([a-zA-Z_$][a-zA-Z0-9_$]*)":', r"\1\2:", body, flags=re.MULTILINE)
    return f"/** @type {{import('tailwindcss').Config}} */\nmodule.exports = {body};"


def build_prompt_text(
    *,
    main_filename: str,
    main_code: str,
    demo_code: str | None = None,
    npm_deps: Iterable[str] | None = None,
    registry_deps: list[dict] | None = None,
    tailwind_config: dict | None = None,
) -> str:
    """Assemble the Prompt Text field value.

    Args:
        main_filename:    e.g. 'upgrade-banner.tsx' — displayed above the code.
        main_code:        full source of the main component.
        demo_code:        optional demo.tsx content (21st.dev has these).
        npm_deps:         package names to list in the NPM deps block.
        registry_deps:    list of {'label': str, 'code': str} to inline verbatim.
        tailwind_config:  optional dict to render as a tailwind.config.js block.
    """
    parts: list[str] = [PREAMBLE, "```tsx"]
    parts.append(main_filename)
    parts.append(main_code.rstrip())
    if demo_code:
        parts.append("")
        parts.append("demo.tsx")
        parts.append(demo_code.rstrip())
    parts.append("```")

    if tailwind_config:
        parts.append("")
        parts.append("Extend existing tailwind.config.js with this code:")
        parts.append("```js")
        parts.append(_tailwind_js(tailwind_config))
        parts.append("```")

    if registry_deps:
        parts.append("")
        parts.append("Copy-paste these files for dependencies:")
        for dep in registry_deps:
            parts.append("```tsx")
            parts.append(dep["label"])
            parts.append(dep["code"].rstrip())
            parts.append("```")

    deps_list = [d for d in (npm_deps or []) if d]
    if deps_list:
        parts.append("")
        parts.append("Install NPM dependencies:")
        parts.append("```bash")
        parts.append(", ".join(deps_list))
        parts.append("```")

    parts.append("")
    parts.append(BOILERPLATE)
    return "\n".join(parts).strip() + "\n"
