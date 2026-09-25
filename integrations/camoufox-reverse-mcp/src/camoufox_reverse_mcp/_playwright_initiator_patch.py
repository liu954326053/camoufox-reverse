"""Startup patch: forward engine-level initiator stacks through the Playwright driver.

reverse8 phase12 adds ``initiatorStack`` to the Juggler
``Network.requestWillBeSent`` event (captured chrome-side in the content
process, zero page-world pollution). The bundled Playwright Firefox driver
parses that event but only copies known fields into the ``Request``
initializer it sends to Python, so the stack would be dropped at the driver
boundary. This patch threads the field through:

* ``ffNetworkManager`` (split-file layout) or the FF ``InterceptableRequest2``
  block inside ``coreBundle.js`` (bundled layout): remembers
  ``payload.initiatorStack`` on the server-side ``network.Request``.
* ``networkDispatchers.js`` (split) or the ``RequestDispatcher`` initializer
  inside ``coreBundle.js`` (bundled): adds ``engineInitiatorStack`` to the
  initializer sent to Python, where it is readable as
  ``request._impl_obj._initializer["engineInitiatorStack"]``.
* ``protocol/validator.js`` (split) or the ``scheme.RequestInitializer`` block
  inside ``coreBundle.js`` (bundled): declares ``engineInitiatorStack`` —
  without this, the dispatcher layer strips the undeclared key from the
  initializer before it reaches Python.

Follows the same contract as ``_playwright_patch.py``: exact-token
replacement only, per-rule uniqueness checks, idempotent, never raises —
any failure only prints a warning to stderr and startup continues (the
field is then simply absent and the MCP records it as unavailable).
"""
from __future__ import annotations

import sys
from pathlib import Path

# (anchor, replacement, per-rule idempotency marker)
_SPLIT_FF_ANCHOR = "    this.request.setRawRequestHeaders(null);"
_SPLIT_FF_REPLACEMENT = (
    _SPLIT_FF_ANCHOR
    + "\n    this.request._engineInitiatorStack = payload.initiatorStack || null;"
)

_BUNDLED_FF_ANCHOR = (
    '          internalCauseToResourceType[payload.internalCause] || causeToResourceType[payload.cause] || "other",\n'
    "          payload.method,\n"
    "          postDataBuffer,\n"
    "          payload.headers\n"
    "        );\n"
    "        this.request.setRawRequestHeaders(null);"
)
_BUNDLED_FF_REPLACEMENT = (
    _BUNDLED_FF_ANCHOR
    + "\n        this.request._engineInitiatorStack = payload.initiatorStack || null;"
)

_SPLIT_DISP_ANCHOR = "      isNavigationRequest: request.isNavigationRequest(),"
_SPLIT_DISP_REPLACEMENT = (
    _SPLIT_DISP_ANCHOR
    + "\n      engineInitiatorStack: request._engineInitiatorStack || null,"
)

_BUNDLED_DISP_ANCHOR = "          isNavigationRequest: request2.isNavigationRequest(),"
_BUNDLED_DISP_REPLACEMENT = (
    _BUNDLED_DISP_ANCHOR
    + "\n          engineInitiatorStack: request2._engineInitiatorStack || null,"
)

# The dispatcher layer strips undeclared initializer keys (validator.js /
# scheme.RequestInitializer in coreBundle.js), so the new field must be
# declared. tOptional(tAny) accepts both undefined (absent) and null.
_SPLIT_VAL_ANCHOR = (
    "  isNavigationRequest: import_validatorPrimitives.tBoolean,\n"
    "  redirectedFrom:"
)
_SPLIT_VAL_REPLACEMENT = (
    "  isNavigationRequest: import_validatorPrimitives.tBoolean,\n"
    "  engineInitiatorStack: (0, import_validatorPrimitives.tOptional)(import_validatorPrimitives.tAny),\n"
    "  redirectedFrom:"
)

_BUNDLED_VAL_ANCHOR = (
    "      isNavigationRequest: tBoolean,\n"
    "      redirectedFrom: tOptional(tChannel([\"Request\"]))"
)
_BUNDLED_VAL_REPLACEMENT = (
    "      isNavigationRequest: tBoolean,\n"
    "      engineInitiatorStack: tOptional(tAny),\n"
    "      redirectedFrom: tOptional(tChannel([\"Request\"]))"
)

_RULES = (
    # (file name, anchor, replacement, marker)
    # 注意 coreBundle.js 有三条规则共用同一文件，marker 必须各自唯一，
    # 不能互相是对方的子串。
    ("ffNetworkManager.js", _SPLIT_FF_ANCHOR, _SPLIT_FF_REPLACEMENT, "_engineInitiatorStack"),
    ("coreBundle.js", _BUNDLED_FF_ANCHOR, _BUNDLED_FF_REPLACEMENT, "request._engineInitiatorStack = payload.initiatorStack"),
    ("networkDispatchers.js", _SPLIT_DISP_ANCHOR, _SPLIT_DISP_REPLACEMENT, "engineInitiatorStack: request._engineInitiatorStack"),
    ("coreBundle.js", _BUNDLED_DISP_ANCHOR, _BUNDLED_DISP_REPLACEMENT, "engineInitiatorStack: request2._engineInitiatorStack"),
    ("validator.js", _SPLIT_VAL_ANCHOR, _SPLIT_VAL_REPLACEMENT, "engineInitiatorStack: (0, import_validatorPrimitives.tOptional)"),
    ("coreBundle.js", _BUNDLED_VAL_ANCHOR, _BUNDLED_VAL_REPLACEMENT, "engineInitiatorStack: tOptional(tAny)"),
)


def _driver_lib_root() -> Path | None:
    """Locate <playwright>/driver/package/lib, or None if unavailable."""
    try:
        import playwright

        root = Path(playwright.__file__).parent / "driver" / "package" / "lib"
        return root if root.is_dir() else None
    except Exception:
        return None


def _apply_rule(path: Path, anchor: str, replacement: str, marker: str) -> bool:
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return False
    if marker in text:
        return False  # Already patched (idempotency).
    if text.count(anchor) != 1:
        return False  # Anchor missing or ambiguous on this driver build; never guess.
    try:
        path.write_text(text.replace(anchor, replacement), encoding="utf-8")
        return True
    except Exception as e:
        print(
            f"[camoufox-reverse-mcp] could not write initiator-stack patch to "
            f"{path.name}: {e}",
            file=sys.stderr,
        )
        return False


def patch_playwright_initiator_stack() -> None:
    """Best-effort startup patch threading Juggler initiatorStack to Python.

    Safe to call unconditionally on every launch. Never raises.
    """
    try:
        root = _driver_lib_root()
        if root is None:
            return
        patched: list[str] = []
        for name, anchor, replacement, marker in _RULES:
            for js in root.rglob(name):
                if _apply_rule(js, anchor, replacement, marker):
                    patched.append(js.name)
        if patched:
            print(
                "[camoufox-reverse-mcp] patched Playwright driver for engine "
                f"initiator stacks in: {', '.join(sorted(set(patched)))}",
                file=sys.stderr,
            )
    except Exception as e:
        # Absolutely never let the patch break server startup.
        print(
            f"[camoufox-reverse-mcp] initiator-stack driver patch skipped: {e}",
            file=sys.stderr,
        )
