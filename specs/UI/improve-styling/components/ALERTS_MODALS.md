# Alerts & Modals

Defines inline alerts, readonly notices, and Bootstrap modal surfaces.

## Polymarket Reference Values

- Alerts sit on the same surface system as cards and inputs
- Modals use the same dark surface with a stronger backdrop
- Buttons inside modals follow the regular button system

## Current Dashboard Mapping

- `.pm-inline-message .alert`
- `.pm-readonly-alert.alert`
- `dbc.Modal` in wallet management and changes flow

## Delta From Current Dashboard

| Area | Current | Target |
| --- | --- | --- |
| Alerts | Generic bootstrap look with soft overrides | Full surface/border token alignment |
| Modal chrome | Mostly Bootstrap default | Surface, border, typography aligned to app shell |

## Implementation Snippet

```css
.pm-inline-message .alert,
.pm-readonly-alert.alert,
.modal-content {
  background: var(--pm-surface);
  border: 1px solid var(--pm-border);
  border-radius: var(--pm-radius-lg);
  color: var(--pm-text);
}

.modal-header,
.modal-footer {
  border-color: var(--pm-border-soft);
}

.modal-backdrop.show {
  opacity: 0.65;
}
```
