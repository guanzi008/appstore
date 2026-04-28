from __future__ import annotations

from dataclasses import dataclass


SUPPORTED_STEP_ACTIONS = (
    "wait-window",
    "activate",
    "sleep",
    "screenshot",
    "key",
    "type",
    "click",
    "click-text",
)


@dataclass(frozen=True)
class CaptureStep:
    action: str
    value: str = ""
    seconds: float = 0.0
    x: int | None = None
    y: int | None = None


def default_capture_steps(*, screenshot_count: int = 1) -> tuple[CaptureStep, ...]:
    _normalized_count = max(1, int(screenshot_count))
    return (
        CaptureStep(action="wait-window", seconds=30.0),
        CaptureStep(action="sleep", seconds=2.0),
        CaptureStep(action="screenshot", value=""),
    )


def parse_capture_steps(
    raw_steps: list[str] | tuple[str, ...],
    *,
    default_screenshot_count: int = 1,
) -> tuple[CaptureStep, ...]:
    parsed = [parse_capture_step(raw_step) for raw_step in raw_steps if str(raw_step).strip()]
    if not parsed:
        return default_capture_steps(screenshot_count=default_screenshot_count)
    return tuple(parsed)


def parse_capture_step(raw_step: str) -> CaptureStep:
    normalized = str(raw_step).strip()
    if not normalized:
        raise ValueError("empty capture step")

    action, separator, remainder = normalized.partition(":")
    action = action.strip().lower()
    value = remainder.strip() if separator else ""

    if action not in SUPPORTED_STEP_ACTIONS:
        raise ValueError(f"unsupported capture step: {action}")

    if action == "wait-window":
        return CaptureStep(action=action, seconds=_float_value(value, default=30.0, label=action))
    if action == "sleep":
        return CaptureStep(action=action, seconds=_float_value(value, default=1.0, label=action))
    if action == "activate":
        _reject_value(action=action, value=value)
        return CaptureStep(action=action)
    if action == "screenshot":
        return CaptureStep(action=action, value=value)
    if action in {"key", "type", "click-text"}:
        if not value:
            raise ValueError(f"{action} requires a value")
        return CaptureStep(action=action, value=value)
    if action == "click":
        x_text, comma, y_text = value.partition(",")
        if not comma:
            raise ValueError("click requires coordinates formatted as x,y")
        try:
            x = int(x_text.strip())
            y = int(y_text.strip())
        except ValueError as exc:
            raise ValueError("click coordinates must be integers") from exc
        return CaptureStep(action=action, x=x, y=y)

    raise ValueError(f"unsupported capture step: {action}")


def _float_value(value: str, *, default: float, label: str) -> float:
    if not value:
        return default
    try:
        number = float(value)
    except ValueError as exc:
        raise ValueError(f"{label} requires a numeric value") from exc
    if number < 0:
        raise ValueError(f"{label} requires a non-negative value")
    return number


def _reject_value(*, action: str, value: str) -> None:
    if value:
        raise ValueError(f"{action} does not accept a value")
