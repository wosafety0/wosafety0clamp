# Nanodesu Allowlist Proposal: Minimal Poi Session Evidence

Date: 2026-10-22
Status: proposal only; no third-party Nanodesu repository was modified.

## Goal

Expose only the session-evidence object emitted by the optional KancolleBot Poi extension. Do not allow the entire `ext` root or the extension's whole reducer state.

## Required upstream change

In `src/paths.ts`, add a reviewed package entry to `ALLOWED_EXT_PLUGINS`:

```ts
'poi-plugin-kancollebot-session-evidence':
  'single minimal session evidence object; no game data or request/response bodies',
```

Add the one permitted subtree to `ALLOWED_EXT_SUBPATHS`:

```ts
'poi-plugin-kancollebot-session-evidence': ['_.evidence'],
```

The extension reducer nests its state at `evidence`, so the read path is:

```text
ext.poi-plugin-kancollebot-session-evidence._.evidence
```

The underscore is inserted by Poi's reducer wrapper. The extension must not put any unrelated data beside the evidence object or put raw response data inside it.

## Required tests before deployment

1. A query of `ext` remains denied.
2. A query of `ext.poi-plugin-kancollebot-session-evidence` remains denied.
3. A query of `ext.poi-plugin-kancollebot-session-evidence._` remains denied.
4. The exact `_.evidence` subtree is readable and serializable.
5. An unknown sibling path is denied.
6. An absent/disabled extension returns the existing not-installed/missing-state behavior.
7. The output contains only `bridgeInstanceId`, `responsePath`, `responseOk`, `observedAt`, `invalidated`, and optional `reason`; no fleet, ship, request, response, account, cookie, or credential data.
8. Existing Nanodesu tests pass; loopback binding and current MCP authentication behavior remain unchanged.

## Security boundary

This proposal does not install or modify Nanodesu, widen the `ext` root, add a network listener, or authorize game actions. It requires a separately reviewed change in the Nanodesu plugin repository and a local integration test before the Python reader can consume this evidence.

## Source

- https://github.com/momocow/poi-plugin-nanodesu-mcp/blob/main/src/paths.ts
- https://github.com/poooi/poi/blob/master/views/redux/reducer-factory.ts
