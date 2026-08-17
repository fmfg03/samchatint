# RQF-SAMCHAT-ASSISTANT-052W ? Spec

## Module

`samchat.assistant.specialist_task_registry`

## Data model

`SpecialistTaskRegistration` contains:

- `task_id`
- `title`
- `agent_type`
- `case_type`
- `status`: `enabled`, `disabled`, or `deprecated`
- `version`
- `tags`
- `required_any`
- `signals`
- `min_signal_count`

## Functions

- `build_specialist_task_registry()` returns a tuple of registrations.
- `specialist_task_ids(include_disabled=False)` returns known task IDs.
- `get_specialist_task_registration(task_id)` returns a registration or `None`.
- `route_specialist_task_from_text(text)` returns exactly one enabled task id or `None`.
- `validate_specialist_task_registry(seed_task_ids=None)` returns a report dictionary.
- `build_specialist_task_registry_report()` returns compact inventory/status counts.

## Routing rules

Routing requires:

1. A natural preview action term from the preview surface.
2. Exactly one enabled registration matching `required_any`.
3. At least `min_signal_count` matched signals.

Ambiguous matches fail closed.

## Integration

`specialist_preview_surface.py` keeps message parsing and explicit task-id detection, but delegates natural routing and task inventory to the registry.

## Tests

- Registry has all 10 seed task IDs.
- Registry report is read-only inventory.
- Natural routing still maps the existing prompts.
- Ambiguous routing returns `None`.
- Disabled tasks are not routable.
